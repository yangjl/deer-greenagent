"""Append-only journal for memory scope migrations.

Three properties come from this file:

*idempotent*   — a completed record short-circuits a repeat of the same work;
*restartable*  — ``started`` is written **before** storage is touched, so an
                 interrupted run is retried rather than silently skipped;
*reversible*   — a completed share can be undone and the undo is itself
                 journaled, so a rollback never runs twice.

One journal per project. The file lives beside the memory store but outside
``users/`` so it is never mistaken for a memory bucket.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deerflow.agents.memory.scopes.adapter import project_digest

logger = logging.getLogger(__name__)

JOURNAL_VERSION = 1
STATUS_STARTED = "started"
STATUS_COMPLETED = "completed"
OPERATION_ROLLBACK = "rollback"
_LOCK_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class JournalRecord:
    """One durable step of a migration."""

    version: int
    operation: str
    status: str
    fact_id: str
    source_bucket_id: str
    agent_name: str
    source_sha256: str
    target_bucket_id: str | None
    target_fact_id: str | None
    recorded_at: str

    @property
    def key(self) -> tuple[str, str, str, str, str, str | None]:
        """Identity of the work this record describes.

        Includes ``source_sha256`` so a *changed* source is a different unit of
        work rather than an already-satisfied one.
        """
        return (
            self.operation,
            self.source_bucket_id,
            self.agent_name,
            self.fact_id,
            self.source_sha256,
            self.target_fact_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MigrationJournal:
    """Durable per-project record of migration decisions and their effects."""

    def __init__(self, root: Path | str, project_id: str) -> None:
        self._directory = Path(root) / project_digest(project_id)
        self._path = self._directory / "journal.jsonl"
        self._project_id = project_id

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Hold a cross-process lock for one complete migration operation."""
        self._directory.mkdir(parents=True, exist_ok=True)
        lock_path = self._directory / "migration.lock"
        handle = lock_path.open("a+b")
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        acquired = False
        try:
            while not acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0, os.SEEK_END)
                        if handle.tell() == 0:
                            handle.write(b"0")
                            handle.flush()
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out acquiring memory migration lock {lock_path}")
                    time.sleep(0.05)
            yield
        finally:
            if acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    logger.warning("Failed to release memory migration lock %s", lock_path)
            handle.close()

    # -- reading ---------------------------------------------------------

    def records(self) -> tuple[JournalRecord, ...]:
        """Return every well-formed record, oldest first.

        A corrupt line is logged and skipped rather than aborting recovery: a
        half-written tail is exactly what a crashed run leaves behind, and the
        readable prefix is still the truth about what already happened.
        """
        if not self._path.is_file():
            return ()
        parsed: list[JournalRecord] = []
        try:
            raw_lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            logger.warning("Unreadable memory migration journal: %s", self._path, exc_info=True)
            return ()
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                parsed.append(JournalRecord(**payload))
            except (json.JSONDecodeError, TypeError, ValueError):
                logger.warning("Skipping malformed memory migration journal line in %s", self._path)
        return tuple(parsed)

    def completed_operations(self) -> tuple[JournalRecord, ...]:
        """Completed, not-yet-rolled-back records, oldest first."""
        records = self.records()
        # A rollback is keyed by the target it removed, so it also cancels a
        # sibling operation (share vs. edit-then-share) writing the same target.
        rolled_back = {record.key[1:] for record in records if record.operation == OPERATION_ROLLBACK and record.status == STATUS_COMPLETED}
        return tuple(record for record in records if record.status == STATUS_COMPLETED and record.operation != OPERATION_ROLLBACK and record.key[1:] not in rolled_back)

    def is_completed(self, *, operation: str, entry: Any, target_fact_id: str | None) -> bool:
        """True when this exact unit of work is already durably done."""
        candidate = (
            operation,
            entry.bucket_id,
            entry.agent_name,
            entry.fact_id,
            entry.sha256,
            target_fact_id,
        )
        return any(record.key == candidate for record in self.completed_operations())

    def completed_for_entry(self, entry: Any) -> tuple[JournalRecord, ...]:
        """Completed decisions bound to the exact inventoried source bytes."""
        return tuple(
            record
            for record in self.completed_operations()
            if (
                record.source_bucket_id,
                record.agent_name,
                record.fact_id,
                record.source_sha256,
            )
            == (entry.bucket_id, entry.agent_name, entry.fact_id, entry.sha256)
        )

    def unresolved_started_for_entry(self, entry: Any) -> tuple[JournalRecord, ...]:
        """Started decisions that never reached their matching completion."""
        records = self.records()
        completed_keys = {record.key for record in records if record.status == STATUS_COMPLETED and record.operation != OPERATION_ROLLBACK}
        return tuple(
            record
            for record in records
            if record.status == STATUS_STARTED
            and record.key not in completed_keys
            and (
                record.source_bucket_id,
                record.agent_name,
                record.fact_id,
                record.source_sha256,
            )
            == (entry.bucket_id, entry.agent_name, entry.fact_id, entry.sha256)
        )

    # -- writing ---------------------------------------------------------

    def _append(self, record: JournalRecord) -> JournalRecord:
        self._directory.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True)
        # Append + flush + fsync: the whole restartability guarantee rests on
        # this line surviving the crash that interrupted the work it describes.
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def _record(
        self,
        *,
        operation: str,
        status: str,
        entry: Any,
        target_bucket_id: str | None,
        target_fact_id: str | None,
    ) -> JournalRecord:
        return self._append(
            JournalRecord(
                version=JOURNAL_VERSION,
                operation=operation,
                status=status,
                fact_id=entry.fact_id,
                source_bucket_id=entry.bucket_id,
                agent_name=entry.agent_name,
                source_sha256=entry.sha256,
                target_bucket_id=target_bucket_id,
                target_fact_id=target_fact_id,
                recorded_at=datetime.now(UTC).isoformat(),
            )
        )

    def record_started(
        self,
        *,
        operation: str,
        entry: Any,
        target_bucket_id: str | None = None,
        target_fact_id: str | None = None,
    ) -> JournalRecord:
        return self._record(
            operation=operation,
            status=STATUS_STARTED,
            entry=entry,
            target_bucket_id=target_bucket_id,
            target_fact_id=target_fact_id,
        )

    def record_completed(
        self,
        *,
        operation: str,
        entry: Any,
        target_bucket_id: str | None = None,
        target_fact_id: str | None = None,
    ) -> JournalRecord:
        return self._record(
            operation=operation,
            status=STATUS_COMPLETED,
            entry=entry,
            target_bucket_id=target_bucket_id,
            target_fact_id=target_fact_id,
        )

    def record_rollback(self, original: JournalRecord) -> JournalRecord:
        """Journal the undo of *original* so a rollback cannot run twice."""
        return self._append(
            JournalRecord(
                version=JOURNAL_VERSION,
                operation=OPERATION_ROLLBACK,
                status=STATUS_COMPLETED,
                fact_id=original.fact_id,
                source_bucket_id=original.source_bucket_id,
                agent_name=original.agent_name,
                source_sha256=original.source_sha256,
                target_bucket_id=original.target_bucket_id,
                target_fact_id=original.target_fact_id,
                recorded_at=datetime.now(UTC).isoformat(),
            )
        )
