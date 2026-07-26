"""Phase 2: the migration engine must be idempotent, restartable, reversible.

No fact becomes shared project memory without an explicit human decision, and
every decision is journaled before it touches storage so an interrupted run
resumes instead of double-applying.
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from deerflow.agents.memory.scope import scoped_memory_user_id
from deerflow.agents.memory.scopes import bind_scope, shared_project_scope
from deerflow.agents.memory.scopes.classify import ProjectAuthority
from deerflow.agents.memory.scopes.inventory import build_manifest
from deerflow.agents.memory.scopes.journal import MigrationJournal
from deerflow.agents.memory.scopes.migration import (
    Decision,
    MigrationContext,
    MigrationOutcome,
    apply_decision,
    plan_migration,
    rollback_migration,
    shared_fact_id,
)

PROJECT = "project-a"
AUTHORITIES = (
    ProjectAuthority(user_id="user-1", project_id=PROJECT),
    ProjectAuthority(user_id="user-2", project_id=PROJECT),
)
SHARED_BUCKET = bind_scope(shared_project_scope(PROJECT)).user_id


class FakeFactStore:
    """Structurally matches ``FileMemoryStorage``'s fact CRUD surface."""

    def __init__(self) -> None:
        self.buckets: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self.upsert_calls = 0

    def get_fact(self, fact_id: str, *, user_id: str, agent_name: str) -> dict[str, Any] | None:
        return self.buckets.get((user_id, agent_name), {}).get(fact_id)

    def upsert_fact(self, fact: dict[str, Any], *, user_id: str, agent_name: str) -> dict[str, Any]:
        self.upsert_calls += 1
        stored = dict(fact)
        stored["scope"] = {"userId": user_id, "agentName": agent_name}
        self.buckets.setdefault((user_id, agent_name), {})[stored["id"]] = stored
        return stored

    def delete_fact(self, fact_id: str, *, user_id: str, agent_name: str) -> dict[str, Any]:
        self.buckets.get((user_id, agent_name), {}).pop(fact_id, None)
        return {"complete": False}


def _write_fact(root: Path, bucket: str, fact_id: str, *, category: str = "context") -> None:
    prefix = hashlib.sha256(fact_id.encode("utf-8")).hexdigest()[:2]
    path = root / "users" / bucket / "agents" / "__default__" / "facts" / prefix / f"{fact_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {fact_id}\ncategory: {category}\nconfidence: 0.9\nrevision: 1\n---\n\n# Title\n\nTropical lines flower late here.\n",
        encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> tuple[Any, FakeFactStore, MigrationContext]:
    bucket_1 = scoped_memory_user_id("user-1", {"project_id": PROJECT})
    _write_fact(tmp_path, bucket_1, "fact_ctx", category="context")
    _write_fact(tmp_path, bucket_1, "fact_id", category="identity")
    _write_fact(tmp_path, "user-1", "fact_personal", category="context")
    _write_fact(tmp_path, "ghost--project--" + "0" * 24, "fact_orphan")

    store = FakeFactStore()
    for fact_id, category in (("fact_ctx", "context"), ("fact_id", "identity")):
        store.buckets.setdefault((bucket_1, "__default__"), {})[fact_id] = {
            "id": fact_id,
            "content": "Tropical lines flower late here.",
            "category": category,
            "confidence": 0.9,
            "revision": 1,
        }
    manifest = build_manifest(tmp_path, AUTHORITIES, known_user_ids=("user-1", "user-2"))
    context = MigrationContext(
        store=store,
        journal=MigrationJournal(tmp_path / ".scope-migration", PROJECT),
        project_id=PROJECT,
        storage_root=tmp_path,
    )
    return manifest, store, context


def _entry(manifest: Any, fact_id: str) -> Any:
    return next(fact for fact in manifest.facts if fact.fact_id == fact_id)


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def test_plan_counts_are_privacy_safe_and_exclude_other_projects(tmp_path: Path) -> None:
    manifest, _, _ = _fixture(tmp_path)

    plan = plan_migration(manifest, PROJECT)

    assert plan.counts.private_legacy == 2
    assert plan.counts.suggested_for_project == 1  # only the 'context' fact
    assert plan.counts.already_project_scoped == 0
    # Unknown buckets cannot safely be attributed to this project, so their
    # existence is not exposed in a project member's plan.
    assert plan.counts.needs_classification == 0
    # The personal (non-project) bucket is not part of this project's plan.
    assert all(suggestion.fact_id != "fact_personal" for suggestion in plan.suggestions)


def test_suggestions_are_scoped_to_the_asking_user(tmp_path: Path) -> None:
    """A member must never see another member's private facts."""
    bucket_2 = scoped_memory_user_id("user-2", {"project_id": PROJECT})
    _write_fact(tmp_path, bucket_2, "fact_other", category="context")
    manifest, _, _ = _fixture(tmp_path)

    plan = plan_migration(manifest, PROJECT)

    assert {item.fact_id for item in plan.suggestions_for("user-1")} == {"fact_ctx", "fact_id"}
    assert {item.fact_id for item in plan.suggestions_for("user-2")} == {"fact_other"}


def test_personal_categories_default_to_keep_private(tmp_path: Path) -> None:
    """Sharing is the direction that leaks, so it is never the safe default."""
    manifest, _, _ = _fixture(tmp_path)

    by_id = {item.fact_id: item for item in plan_migration(manifest, PROJECT).suggestions}

    assert by_id["fact_ctx"].suggested_decision is Decision.SHARE
    assert by_id["fact_id"].suggested_decision is Decision.KEEP_PRIVATE
    assert by_id["fact_id"].reason


def test_already_shared_facts_are_counted_not_re_suggested(tmp_path: Path) -> None:
    _write_fact(tmp_path, SHARED_BUCKET, "shared_1", category="context")
    manifest, _, _ = _fixture(tmp_path)

    plan = plan_migration(manifest, PROJECT)

    assert plan.counts.already_project_scoped == 1
    assert all(item.fact_id != "shared_1" for item in plan.suggestions)


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------


def test_share_copies_into_the_shared_bucket_and_leaves_the_original(tmp_path: Path) -> None:
    manifest, store, context = _fixture(tmp_path)
    entry = _entry(manifest, "fact_ctx")

    result = apply_decision(entry, Decision.SHARE, context)

    assert result.outcome is MigrationOutcome.APPLIED
    target_id = shared_fact_id(entry.bucket_id, "fact_ctx")
    assert store.buckets[(SHARED_BUCKET, "__default__")][target_id]["content"]
    # Copy, never move: rollback has something to restore to.
    assert store.buckets[(entry.bucket_id, "__default__")]["fact_ctx"]


def test_shared_copy_records_its_provenance(tmp_path: Path) -> None:
    manifest, store, context = _fixture(tmp_path)
    entry = _entry(manifest, "fact_ctx")

    apply_decision(entry, Decision.SHARE, context)

    target = store.buckets[(SHARED_BUCKET, "__default__")][shared_fact_id(entry.bucket_id, "fact_ctx")]
    assert target["sharedFrom"]["bucket_id"] == entry.bucket_id
    assert target["sharedFrom"]["sha256"] == entry.sha256


def test_share_is_idempotent_across_repeated_calls(tmp_path: Path) -> None:
    manifest, store, context = _fixture(tmp_path)
    entry = _entry(manifest, "fact_ctx")

    first = apply_decision(entry, Decision.SHARE, context)
    writes_after_first = store.upsert_calls
    second = apply_decision(entry, Decision.SHARE, context)

    assert first.outcome is MigrationOutcome.APPLIED
    assert second.outcome is MigrationOutcome.ALREADY_APPLIED
    assert store.upsert_calls == writes_after_first


def test_a_second_conflicting_decision_cannot_overwrite_a_shared_copy(tmp_path: Path) -> None:
    manifest, store, context = _fixture(tmp_path)
    entry = _entry(manifest, "fact_ctx")
    apply_decision(entry, Decision.SHARE, context)

    result = apply_decision(
        entry,
        Decision.EDIT_THEN_SHARE,
        context,
        edited_content="A different approval payload.",
    )

    assert result.outcome is MigrationOutcome.DECISION_CONFLICT
    target = store.buckets[(SHARED_BUCKET, "__default__")][shared_fact_id(entry.bucket_id, entry.fact_id)]
    assert target["content"] != "A different approval payload."


def test_concurrent_conflicting_decisions_produce_exactly_one_write(tmp_path: Path) -> None:
    manifest, store, context = _fixture(tmp_path)
    entry = _entry(manifest, "fact_ctx")

    with ThreadPoolExecutor(max_workers=2) as executor:
        share = executor.submit(apply_decision, entry, Decision.SHARE, context)
        edit = executor.submit(
            apply_decision,
            entry,
            Decision.EDIT_THEN_SHARE,
            context,
            edited_content="Concurrent edited approval.",
        )
        outcomes = {share.result().outcome, edit.result().outcome}

    assert outcomes == {MigrationOutcome.APPLIED, MigrationOutcome.DECISION_CONFLICT}
    assert store.upsert_calls == 1


def test_interrupted_run_resumes_from_the_journal(tmp_path: Path) -> None:
    """A 'started' record with no 'completed' must be retried, not skipped."""
    manifest, store, context = _fixture(tmp_path)
    entry = _entry(manifest, "fact_ctx")
    context.journal.record_started(
        operation=Decision.SHARE.value,
        entry=entry,
        target_bucket_id=SHARED_BUCKET,
        target_fact_id=shared_fact_id(entry.bucket_id, entry.fact_id),
    )

    result = apply_decision(entry, Decision.SHARE, context)

    assert result.outcome is MigrationOutcome.APPLIED
    assert store.upsert_calls == 1


def test_interrupted_decision_cannot_resume_as_a_different_decision(tmp_path: Path) -> None:
    manifest, store, context = _fixture(tmp_path)
    entry = _entry(manifest, "fact_ctx")
    context.journal.record_started(
        operation=Decision.SHARE.value,
        entry=entry,
        target_bucket_id=SHARED_BUCKET,
        target_fact_id=shared_fact_id(entry.bucket_id, entry.fact_id),
    )

    result = apply_decision(
        entry,
        Decision.EDIT_THEN_SHARE,
        context,
        edited_content="Conflicting retry.",
    )

    assert result.outcome is MigrationOutcome.DECISION_CONFLICT
    assert store.upsert_calls == 0


def test_a_changed_source_fact_refuses_to_apply(tmp_path: Path) -> None:
    """Same discipline as the Phase 1 gates: a stale artifact never satisfies."""
    manifest, store, context = _fixture(tmp_path)
    entry = _entry(manifest, "fact_ctx")
    stale = type(entry)(**{**entry.to_dict(), "sha256": "0" * 64})

    result = apply_decision(stale, Decision.SHARE, context)

    assert result.outcome is MigrationOutcome.SOURCE_CHANGED
    assert store.upsert_calls == 0


def test_a_source_change_during_storage_read_refuses_to_apply(tmp_path: Path) -> None:
    manifest, store, context = _fixture(tmp_path)
    entry = _entry(manifest, "fact_ctx")
    original_get = store.get_fact

    def changing_get(fact_id: str, *, user_id: str, agent_name: str):
        source = original_get(fact_id, user_id=user_id, agent_name=agent_name)
        (tmp_path / entry.relative_path).write_text(
            "---\nid: fact_ctx\ncategory: context\n---\n\n# Changed\n\nChanged during read.\n",
            encoding="utf-8",
        )
        return {**source, "content": "Changed during read."} if source else None

    store.get_fact = changing_get  # type: ignore[method-assign]

    result = apply_decision(entry, Decision.SHARE, context)

    assert result.outcome is MigrationOutcome.SOURCE_CHANGED
    assert store.upsert_calls == 0


def test_a_vanished_source_fact_is_reported_not_crashed(tmp_path: Path) -> None:
    manifest, store, context = _fixture(tmp_path)
    entry = _entry(manifest, "fact_ctx")
    (tmp_path / entry.relative_path).unlink()

    result = apply_decision(entry, Decision.SHARE, context)

    assert result.outcome is MigrationOutcome.SOURCE_MISSING
    assert store.upsert_calls == 0


def test_edit_then_share_stores_the_edited_content_only(tmp_path: Path) -> None:
    manifest, store, context = _fixture(tmp_path)
    entry = _entry(manifest, "fact_ctx")

    apply_decision(entry, Decision.EDIT_THEN_SHARE, context, edited_content="Redacted: lines flower late.")

    target = store.buckets[(SHARED_BUCKET, "__default__")][shared_fact_id(entry.bucket_id, entry.fact_id)]
    assert target["content"] == "Redacted: lines flower late."
    assert store.buckets[(entry.bucket_id, "__default__")]["fact_ctx"]["content"] != target["content"]


def test_edit_then_share_requires_content(tmp_path: Path) -> None:
    manifest, _, context = _fixture(tmp_path)

    with pytest.raises(ValueError):
        apply_decision(_entry(manifest, "fact_ctx"), Decision.EDIT_THEN_SHARE, context)


@pytest.mark.parametrize("decision", [Decision.KEEP_PRIVATE, Decision.QUARANTINE])
def test_non_sharing_decisions_touch_no_storage(tmp_path: Path, decision: Decision) -> None:
    manifest, store, context = _fixture(tmp_path)

    result = apply_decision(_entry(manifest, "fact_ctx"), decision, context)

    assert result.outcome is MigrationOutcome.RECORDED
    assert store.upsert_calls == 0
    assert store.buckets.get((SHARED_BUCKET, "__default__")) is None


def test_shared_fact_ids_do_not_collide_across_owners() -> None:
    """Two members can hold the same fact id; the shared bucket must not merge them."""
    bucket_1 = scoped_memory_user_id("user-1", {"project_id": PROJECT})
    bucket_2 = scoped_memory_user_id("user-2", {"project_id": PROJECT})

    assert shared_fact_id(bucket_1, "fact_ctx") != shared_fact_id(bucket_2, "fact_ctx")
    assert shared_fact_id(bucket_1, "fact_ctx") == shared_fact_id(bucket_1, "fact_ctx")


# --------------------------------------------------------------------------
# Reversing
# --------------------------------------------------------------------------


def test_rollback_removes_shared_copies_and_restores_privacy(tmp_path: Path) -> None:
    manifest, store, context = _fixture(tmp_path)
    entry = _entry(manifest, "fact_ctx")
    apply_decision(entry, Decision.SHARE, context)

    reverted = rollback_migration(context)

    assert reverted == 1
    assert store.buckets.get((SHARED_BUCKET, "__default__")) == {}
    assert store.buckets[(entry.bucket_id, "__default__")]["fact_ctx"]


def test_rollback_then_rerun_reproduces_the_same_shared_state(tmp_path: Path) -> None:
    manifest, store, context = _fixture(tmp_path)
    entry = _entry(manifest, "fact_ctx")

    apply_decision(entry, Decision.SHARE, context)
    before = dict(store.buckets[(SHARED_BUCKET, "__default__")])
    rollback_migration(context)
    apply_decision(entry, Decision.SHARE, context)

    assert store.buckets[(SHARED_BUCKET, "__default__")].keys() == before.keys()


def test_rollback_is_itself_idempotent(tmp_path: Path) -> None:
    manifest, _, context = _fixture(tmp_path)
    apply_decision(_entry(manifest, "fact_ctx"), Decision.SHARE, context)

    assert rollback_migration(context) == 1
    assert rollback_migration(context) == 0


def test_rollback_can_be_limited_to_one_owners_source_bucket(tmp_path: Path) -> None:
    bucket_2 = scoped_memory_user_id("user-2", {"project_id": PROJECT})
    _write_fact(tmp_path, bucket_2, "fact_other", category="context")
    manifest, store, context = _fixture(tmp_path)
    store.buckets[(bucket_2, "__default__")] = {
        "fact_other": {
            "id": "fact_other",
            "content": "Other member fact.",
            "category": "context",
        }
    }
    first = _entry(manifest, "fact_ctx")
    refreshed = build_manifest(tmp_path, AUTHORITIES, known_user_ids=("user-1", "user-2"))
    second = next(entry for entry in refreshed.facts if entry.fact_id == "fact_other")
    apply_decision(first, Decision.SHARE, context)
    apply_decision(second, Decision.SHARE, context)

    reverted = rollback_migration(context, source_bucket_ids={first.bucket_id})

    assert reverted == 1
    shared = store.buckets[(SHARED_BUCKET, "__default__")]
    assert shared_fact_id(first.bucket_id, first.fact_id) not in shared
    assert shared_fact_id(second.bucket_id, second.fact_id) in shared


def test_journal_survives_a_fresh_process(tmp_path: Path) -> None:
    manifest, store, context = _fixture(tmp_path)
    entry = _entry(manifest, "fact_ctx")
    apply_decision(entry, Decision.SHARE, context)

    reopened = MigrationContext(
        store=store,
        journal=MigrationJournal(tmp_path / ".scope-migration", PROJECT),
        project_id=PROJECT,
        storage_root=tmp_path,
    )

    assert apply_decision(entry, Decision.SHARE, reopened).outcome is MigrationOutcome.ALREADY_APPLIED


def test_journals_of_two_projects_do_not_interfere(tmp_path: Path) -> None:
    manifest, store, context = _fixture(tmp_path)
    apply_decision(_entry(manifest, "fact_ctx"), Decision.SHARE, context)

    other = MigrationContext(
        store=store,
        journal=MigrationJournal(tmp_path / ".scope-migration", "project-b"),
        project_id="project-b",
        storage_root=tmp_path,
    )

    assert other.journal.completed_operations() == ()
    assert rollback_migration(other) == 0
