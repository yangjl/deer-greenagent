"""Phase 2 human exit review, executed against the real DeerMem storage.

    "Two authorized users must retrieve the same approved project fact while
     all unapproved facts remain private. The reviewer must also observe a
     successful rollback and rerun."

Every other Phase 2 test uses a fake fact store to keep the engine isolated.
This one deliberately does not: it drives ``FileMemoryStorage`` so the scope
adapter, the shared-bucket path, DeerMem's own validation, and the read chain
are proven to agree on real files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deerflow.agents.memory.backends.deermem.deermem.config import DeerMemConfig
from deerflow.agents.memory.backends.deermem.deermem.core.storage import create_storage
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
    rollback_migration,
)
from deerflow.agents.memory.scopes.reader import scoped_memory_bucket_chain

PROJECT = "project-g2f"
AGENT = "__default__"
AUTHORITIES = (
    ProjectAuthority(user_id="user-1", project_id=PROJECT),
    ProjectAuthority(user_id="user-2", project_id=PROJECT),
)


def _bucket(user_id: str) -> str:
    return scoped_memory_user_id(user_id, {"project_id": PROJECT})


def _read_chain(storage, user_id: str) -> set[str]:
    """Every fact body *user_id* can retrieve in this project."""
    contents: set[str] = set()
    for bucket in scoped_memory_bucket_chain(user_id, {"project_id": PROJECT}):
        for fact in storage.list_facts(user_id=bucket, agent_name=AGENT):
            contents.add(fact["content"])
    return contents


@pytest.fixture
def storage(tmp_path: Path):
    return create_storage(DeerMemConfig(storage_path=str(tmp_path)))


@pytest.fixture
def seeded(storage, tmp_path: Path):
    """user-1 has one shareable and one private fact; user-2 has their own."""
    storage.upsert_fact(
        {"id": "fact_shareable", "content": "Tropical lines flower late in this nursery.", "category": "context"},
        user_id=_bucket("user-1"),
        agent_name=AGENT,
    )
    storage.upsert_fact(
        {"id": "fact_private", "content": "I prefer terse status updates.", "category": "preference"},
        user_id=_bucket("user-1"),
        agent_name=AGENT,
    )
    storage.upsert_fact(
        {"id": "fact_other", "content": "Plot 12 flooded last season.", "category": "context"},
        user_id=_bucket("user-2"),
        agent_name=AGENT,
    )
    return storage


def _context(storage, tmp_path: Path) -> MigrationContext:
    return MigrationContext(
        store=storage,
        journal=MigrationJournal(tmp_path / ".scope-migration", PROJECT),
        project_id=PROJECT,
        storage_root=tmp_path,
    )


def _entry(tmp_path: Path, fact_id: str, owner: str):
    manifest = build_manifest(tmp_path, AUTHORITIES, known_user_ids=("user-1", "user-2"))
    return next(fact for fact in manifest.facts if fact.fact_id == fact_id and fact.bucket_id == _bucket(owner))


def test_before_any_approval_nothing_crosses_between_members(seeded, tmp_path: Path) -> None:
    assert _read_chain(seeded, "user-1") == {
        "Tropical lines flower late in this nursery.",
        "I prefer terse status updates.",
    }
    assert _read_chain(seeded, "user-2") == {"Plot 12 flooded last season."}


def test_an_approved_fact_becomes_readable_by_the_other_member(seeded, tmp_path: Path) -> None:
    """The exit review, stated directly."""
    result = apply_decision(
        _entry(tmp_path, "fact_shareable", "user-1"),
        Decision.SHARE,
        _context(seeded, tmp_path),
    )

    assert result.outcome is MigrationOutcome.APPLIED
    assert "Tropical lines flower late in this nursery." in _read_chain(seeded, "user-2")


def test_unapproved_facts_stay_private_after_the_approval(seeded, tmp_path: Path) -> None:
    apply_decision(
        _entry(tmp_path, "fact_shareable", "user-1"),
        Decision.SHARE,
        _context(seeded, tmp_path),
    )

    visible_to_other = _read_chain(seeded, "user-2")

    assert "I prefer terse status updates." not in visible_to_other
    # And the approving member did not lose anything of their own.
    assert "I prefer terse status updates." in _read_chain(seeded, "user-1")


def test_a_third_projects_member_sees_none_of_it(seeded, tmp_path: Path) -> None:
    """The Phase 2 no-go: no bucket is shared across two projects."""
    apply_decision(
        _entry(tmp_path, "fact_shareable", "user-1"),
        Decision.SHARE,
        _context(seeded, tmp_path),
    )

    elsewhere = {content for bucket in scoped_memory_bucket_chain("user-1", {"project_id": "project-other"}) for content in (fact["content"] for fact in seeded.list_facts(user_id=bucket, agent_name=AGENT))}

    assert elsewhere == set()


def test_rollback_then_rerun_reproduces_the_same_visibility(seeded, tmp_path: Path) -> None:
    """The reviewer must observe a successful rollback and rerun."""
    context = _context(seeded, tmp_path)
    entry = _entry(tmp_path, "fact_shareable", "user-1")
    shared_bucket = bind_scope(shared_project_scope(PROJECT)).user_id

    apply_decision(entry, Decision.SHARE, context)
    after_first = _read_chain(seeded, "user-2")

    assert rollback_migration(context) == 1
    assert seeded.list_facts(user_id=shared_bucket, agent_name=AGENT) == []
    assert _read_chain(seeded, "user-2") == {"Plot 12 flooded last season."}

    # Rerun: the same decision reproduces exactly the same visibility.
    apply_decision(_entry(tmp_path, "fact_shareable", "user-1"), Decision.SHARE, context)
    assert _read_chain(seeded, "user-2") == after_first


def test_the_private_original_is_never_modified_by_sharing(seeded, tmp_path: Path) -> None:
    before = seeded.get_fact("fact_shareable", user_id=_bucket("user-1"), agent_name=AGENT)

    apply_decision(
        _entry(tmp_path, "fact_shareable", "user-1"),
        Decision.SHARE,
        _context(seeded, tmp_path),
    )
    after = seeded.get_fact("fact_shareable", user_id=_bucket("user-1"), agent_name=AGENT)

    assert before == after


def test_edited_sharing_publishes_only_the_edit(seeded, tmp_path: Path) -> None:
    apply_decision(
        _entry(tmp_path, "fact_private", "user-1"),
        Decision.EDIT_THEN_SHARE,
        _context(seeded, tmp_path),
        edited_content="Team convention: keep status updates terse.",
    )

    visible_to_other = _read_chain(seeded, "user-2")

    assert "Team convention: keep status updates terse." in visible_to_other
    assert "I prefer terse status updates." not in visible_to_other


def test_the_shared_copy_carries_provenance_back_to_the_approved_bytes(seeded, tmp_path: Path) -> None:
    entry = _entry(tmp_path, "fact_shareable", "user-1")
    result = apply_decision(entry, Decision.SHARE, _context(seeded, tmp_path))

    stored = seeded.get_fact(result.target_fact_id, user_id=result.target_bucket_id, agent_name=AGENT)

    assert stored["sharedFrom"]["sha256"] == entry.sha256
    assert stored["sharedFrom"]["bucket_id"] == _bucket("user-1")
