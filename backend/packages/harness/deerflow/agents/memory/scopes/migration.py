"""Plan and apply the DeerMem scope migration.

The engine never decides to share. It *suggests*, a human decides one fact at
a time, and only then does anything move — as a **copy** into the project's
shared bucket, so the private original survives and rollback is exact.

Every write is bracketed by journal records (see ``journal.py``), and a source
fact whose bytes changed since the manifest was taken is refused rather than
migrated on stale evidence.
"""

from __future__ import annotations

import copy
import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from deerflow.agents.memory.scopes.adapter import bind_scope
from deerflow.agents.memory.scopes.classify import BucketClassification
from deerflow.agents.memory.scopes.inventory import FactEntry, ScopeManifest, file_checksum, read_fact_document
from deerflow.agents.memory.scopes.journal import JournalRecord, MigrationJournal
from deerflow.agents.memory.scopes.models import shared_project_scope

logger = logging.getLogger(__name__)

_SHARED_ID_DIGEST_LENGTH = 16

# Categories that describe the *work* generalize to a project; categories that
# describe the *person* do not. Sharing is the direction that leaks, so
# anything unrecognized falls to the private side.
PROJECT_LEANING_CATEGORIES = frozenset({"context", "goal", "constraint", "decision", "correction"})
PERSONAL_LEANING_CATEGORIES = frozenset({"identity", "preference", "behavior"})


class Decision(StrEnum):
    """The four actions a reviewer can take on one fact."""

    KEEP_PRIVATE = "keep_private"
    SHARE = "share"
    EDIT_THEN_SHARE = "edit_then_share"
    QUARANTINE = "quarantine"


class MigrationOutcome(StrEnum):
    """What actually happened when a decision was applied."""

    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    RECORDED = "recorded"
    SOURCE_CHANGED = "source_changed"
    SOURCE_MISSING = "source_missing"
    DECISION_CONFLICT = "decision_conflict"


class FactStore(Protocol):
    """The slice of DeerMem's fact CRUD this engine needs.

    Structurally satisfied by ``FileMemoryStorage``; a narrow protocol keeps
    the engine testable without standing up the whole storage stack.
    """

    def get_fact(self, fact_id: str, *, user_id: str, agent_name: str) -> dict[str, Any] | None: ...

    def upsert_fact(self, fact: dict[str, Any], *, user_id: str, agent_name: str) -> dict[str, Any]: ...

    def delete_fact(self, fact_id: str, *, user_id: str, agent_name: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class MigrationContext:
    """Everything one project's migration needs, bundled and immutable."""

    store: FactStore
    journal: MigrationJournal
    project_id: str
    storage_root: Path


@dataclass(frozen=True, slots=True)
class MigrationSuggestion:
    """One reviewable fact with a suggestion the reviewer may override."""

    fact_id: str
    bucket_id: str
    agent_name: str
    owner_user_id: str
    sha256: str
    category: str
    suggested_decision: Decision
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "bucket_id": self.bucket_id,
            "agent_name": self.agent_name,
            "owner_user_id": self.owner_user_id,
            "sha256": self.sha256,
            "category": self.category,
            "suggested_decision": self.suggested_decision.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class MigrationCounts:
    """The privacy-safe landing view: counts only, never fact bodies."""

    private_legacy: int = 0
    suggested_for_project: int = 0
    already_project_scoped: int = 0
    needs_classification: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "private_legacy": self.private_legacy,
            "suggested_for_project": self.suggested_for_project,
            "already_project_scoped": self.already_project_scoped,
            "needs_classification": self.needs_classification,
        }


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """A project's proposed migration. Nothing here has been applied."""

    project_id: str
    counts: MigrationCounts
    suggestions: tuple[MigrationSuggestion, ...] = field(default_factory=tuple)

    def suggestions_for(self, user_id: str) -> tuple[MigrationSuggestion, ...]:
        """Only this user's own facts — the review queue is never shared."""
        return tuple(item for item in self.suggestions if item.owner_user_id == user_id)

    def for_user(self, user_id: str) -> MigrationPlan:
        """Return the caller's own queue and privacy-safe counts."""
        suggestions = self.suggestions_for(user_id)
        return MigrationPlan(
            project_id=self.project_id,
            counts=MigrationCounts(
                private_legacy=len(suggestions),
                suggested_for_project=sum(1 for item in suggestions if item.suggested_decision is Decision.SHARE),
                already_project_scoped=self.counts.already_project_scoped,
                # Unknown buckets cannot be attributed to this project without
                # guessing, so their existence is not a project-member metric.
                needs_classification=0,
            ),
            suggestions=suggestions,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "counts": self.counts.to_dict(),
            "suggestions": [item.to_dict() for item in self.suggestions],
        }


def shared_fact_id(source_bucket_id: str, fact_id: str) -> str:
    """A deterministic, collision-free id for a fact's shared copy.

    Deterministic so re-running is idempotent; bucket-derived so two members
    holding the same fact id do not silently overwrite each other.
    """
    digest = hashlib.sha256(f"{source_bucket_id}\x00{fact_id}".encode()).hexdigest()
    return f"shared_{digest[:_SHARED_ID_DIGEST_LENGTH]}"


def _fact_category(root: Path, entry: FactEntry) -> str:
    """Read a fact's category; an unparseable fact reads as ``other``."""
    return str(read_fact_document(root, entry.relative_path)["category"])


def _suggest(category: str) -> tuple[Decision, str]:
    if category in PROJECT_LEANING_CATEGORIES:
        return Decision.SHARE, f"Category '{category}' describes the work, so it generalizes to everyone on this project."
    if category in PERSONAL_LEANING_CATEGORIES:
        return Decision.KEEP_PRIVATE, f"Category '{category}' describes you rather than the work."
    return Decision.KEEP_PRIVATE, f"Category '{category}' is unrecognized; the private side is the safe default."


def plan_migration(
    manifest: ScopeManifest,
    project_id: str,
    *,
    completed_operations: Iterable[JournalRecord] = (),
) -> MigrationPlan:
    """Propose per-fact scopes for one project. Applies nothing."""
    root = Path(manifest.storage_root)
    project_buckets = {bucket.bucket_id: bucket for bucket in manifest.buckets if bucket.classification is BucketClassification.PROJECT and bucket.project_id == project_id}
    shared_bucket_id = bind_scope(shared_project_scope(project_id)).user_id

    decided = {
        (
            record.source_bucket_id,
            record.agent_name,
            record.fact_id,
            record.source_sha256,
        )
        for record in completed_operations
    }
    suggestions: list[MigrationSuggestion] = []
    for entry in manifest.facts:
        bucket = project_buckets.get(entry.bucket_id)
        if bucket is None or not bucket.user_id:
            continue
        if (entry.bucket_id, entry.agent_name, entry.fact_id, entry.sha256) in decided:
            continue
        category = _fact_category(root, entry)
        decision, reason = _suggest(category)
        suggestions.append(
            MigrationSuggestion(
                fact_id=entry.fact_id,
                bucket_id=entry.bucket_id,
                agent_name=entry.agent_name,
                owner_user_id=bucket.user_id,
                sha256=entry.sha256,
                category=category,
                suggested_decision=decision,
                reason=reason,
            )
        )

    counts = MigrationCounts(
        private_legacy=len(suggestions),
        suggested_for_project=sum(1 for item in suggestions if item.suggested_decision is Decision.SHARE),
        already_project_scoped=sum(1 for entry in manifest.facts if entry.bucket_id == shared_bucket_id),
        needs_classification=0,
    )
    return MigrationPlan(project_id=project_id, counts=counts, suggestions=tuple(suggestions))


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """The outcome of applying one decision to one fact."""

    outcome: MigrationOutcome
    target_bucket_id: str | None = None
    target_fact_id: str | None = None
    detail: str = ""


def _source_is_unchanged(context: MigrationContext, entry: FactEntry) -> MigrationOutcome | None:
    path = context.storage_root / entry.relative_path
    if not path.is_file():
        return MigrationOutcome.SOURCE_MISSING
    try:
        checksum, _ = file_checksum(path)
    except OSError:
        logger.warning("Unreadable source fact during migration: %s", path, exc_info=True)
        return MigrationOutcome.SOURCE_MISSING
    return None if checksum == entry.sha256 else MigrationOutcome.SOURCE_CHANGED


def _shared_copy(source: dict[str, Any], entry: FactEntry, target_fact_id: str, edited_content: str | None) -> dict[str, Any]:
    copied = copy.deepcopy(source)
    copied["id"] = target_fact_id
    copied.pop("revision", None)
    copied.pop("scope", None)
    if edited_content is not None:
        copied["content"] = edited_content
    # Provenance travels with the copy so a reader can always trace a shared
    # fact back to the exact private bytes a human approved.
    copied["sharedFrom"] = {
        "bucket_id": entry.bucket_id,
        "agent_name": entry.agent_name,
        "fact_id": entry.fact_id,
        "sha256": entry.sha256,
        "edited": edited_content is not None,
    }
    return copied


def _apply_decision_locked(
    entry: FactEntry,
    decision: Decision,
    context: MigrationContext,
    *,
    edited_content: str | None = None,
) -> MigrationResult:
    completed = context.journal.completed_for_entry(entry)
    matching = tuple(record for record in completed if record.operation == decision.value)
    if completed:
        if matching:
            record = matching[-1]
            return MigrationResult(
                MigrationOutcome.ALREADY_APPLIED,
                record.target_bucket_id,
                record.target_fact_id,
            )
        return MigrationResult(
            MigrationOutcome.DECISION_CONFLICT,
            detail="This fact already has a different completed migration decision.",
        )
    started = context.journal.unresolved_started_for_entry(entry)
    if any(record.operation != decision.value for record in started):
        return MigrationResult(
            MigrationOutcome.DECISION_CONFLICT,
            detail="This fact has an interrupted migration using a different decision.",
        )
    if decision in {Decision.KEEP_PRIVATE, Decision.QUARANTINE}:
        context.journal.record_completed(operation=decision.value, entry=entry)
        return MigrationResult(outcome=MigrationOutcome.RECORDED)

    target_bucket_id = bind_scope(shared_project_scope(context.project_id)).user_id
    target_fact_id = shared_fact_id(entry.bucket_id, entry.fact_id)

    stale = _source_is_unchanged(context, entry)
    if stale is not None:
        return MigrationResult(stale, detail=f"Source fact {entry.fact_id!r} no longer matches the inventoried bytes.")

    source = context.store.get_fact(entry.fact_id, user_id=entry.bucket_id, agent_name=entry.agent_name)
    if source is None:
        return MigrationResult(MigrationOutcome.SOURCE_MISSING, detail=f"Source fact {entry.fact_id!r} is not in storage.")
    source = copy.deepcopy(source)
    # The fact store and inventory use different locks. Recheck after the read
    # so a concurrent memory write cannot swap in bytes the human never saw.
    stale = _source_is_unchanged(context, entry)
    if stale is not None:
        return MigrationResult(stale, detail=f"Source fact {entry.fact_id!r} changed while the migration decision was being applied.")

    # Journal first: a crash between here and the write is retried, not lost.
    context.journal.record_started(
        operation=decision.value,
        entry=entry,
        target_bucket_id=target_bucket_id,
        target_fact_id=target_fact_id,
    )
    context.store.upsert_fact(
        _shared_copy(source, entry, target_fact_id, edited_content),
        user_id=target_bucket_id,
        agent_name=entry.agent_name,
    )
    context.journal.record_completed(
        operation=decision.value,
        entry=entry,
        target_bucket_id=target_bucket_id,
        target_fact_id=target_fact_id,
    )
    return MigrationResult(MigrationOutcome.APPLIED, target_bucket_id, target_fact_id)


def apply_decision(
    entry: FactEntry,
    decision: Decision,
    context: MigrationContext,
    *,
    edited_content: str | None = None,
) -> MigrationResult:
    """Apply one reviewer decision to one fact under a durable operation lock.

    ``keep_private`` and ``quarantine`` are journal-only: they record that the
    human has ruled, and touch no memory data at all.
    """
    if decision is Decision.EDIT_THEN_SHARE and not (edited_content or "").strip():
        raise ValueError("edit_then_share requires non-empty edited content.")
    with context.journal.exclusive():
        return _apply_decision_locked(
            entry,
            decision,
            context,
            edited_content=edited_content,
        )


def rollback_migration(
    context: MigrationContext,
    *,
    operations: Iterable[str] = (Decision.SHARE.value, Decision.EDIT_THEN_SHARE.value),
    source_bucket_ids: set[str] | None = None,
) -> int:
    """Remove every shared copy this project's journal says it created.

    Private originals were never mutated, so removing the copies restores the
    exact pre-migration visibility. Each undo is journaled, which makes a
    second rollback a no-op rather than a double delete.
    """
    wanted = set(operations)
    reverted = 0
    with context.journal.exclusive():
        for record in context.journal.completed_operations():
            if record.operation not in wanted or not record.target_bucket_id or not record.target_fact_id:
                continue
            if source_bucket_ids is not None and record.source_bucket_id not in source_bucket_ids:
                continue
            context.store.delete_fact(
                record.target_fact_id,
                user_id=record.target_bucket_id,
                agent_name=record.agent_name,
            )
            context.journal.record_rollback(record)
            reverted += 1
    return reverted
