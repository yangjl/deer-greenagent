"""Inventory legacy DeerMem buckets into a checksum-preserving manifest.

The manifest is the evidence artifact behind Phase 2: it records *which* facts
exist, *where*, and *exactly which bytes* — so a later migration can prove it
carried the same content it inventoried, and an operator can diff two runs.

It deliberately carries no fact bodies. The landing view shows counts; a fact's
content is only ever served to the user who owns it.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from deerflow.agents.memory.scopes.classify import (
    BucketClassification,
    ProjectAuthority,
    build_bucket_index,
    classify_bucket,
)

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1
_FACT_SUFFIX = ".md"
_READ_CHUNK_BYTES = 1024 * 256


@dataclass(frozen=True, slots=True)
class FactEntry:
    """One fact file, identified by content rather than by trust."""

    bucket_id: str
    agent_name: str
    fact_id: str
    relative_path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket_id": self.bucket_id,
            "agent_name": self.agent_name,
            "fact_id": self.fact_id,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class BucketEntry:
    """One on-disk bucket and what it was recomputed to mean."""

    bucket_id: str
    classification: BucketClassification
    user_id: str | None
    project_id: str | None
    agent_names: tuple[str, ...]
    fact_count: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket_id": self.bucket_id,
            "classification": self.classification.value,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "agent_names": list(self.agent_names),
            "fact_count": self.fact_count,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ScopeManifest:
    """A deterministic, content-addressed view of one DeerMem storage root."""

    version: int
    storage_root: str
    buckets: tuple[BucketEntry, ...]
    facts: tuple[FactEntry, ...]
    skipped_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "storage_root": self.storage_root,
            "buckets": [bucket.to_dict() for bucket in self.buckets],
            "facts": [fact.to_dict() for fact in self.facts],
            "skipped_paths": list(self.skipped_paths),
        }

    def facts_for_bucket(self, bucket_id: str) -> tuple[FactEntry, ...]:
        return tuple(fact for fact in self.facts if fact.bucket_id == bucket_id)

    def for_project_member(self, project_id: str, user_id: str) -> ScopeManifest:
        """Return the manifest slice this project member may download."""
        visible_buckets = tuple(
            bucket for bucket in self.buckets if bucket.project_id == project_id and ((bucket.classification is BucketClassification.PROJECT and bucket.user_id == user_id) or bucket.classification is BucketClassification.SHARED_PROJECT)
        )
        visible_ids = {bucket.bucket_id for bucket in visible_buckets}
        visible_facts = tuple(fact for fact in self.facts if fact.bucket_id in visible_ids)
        visible_skipped = tuple(path for path in self.skipped_paths if len(path.split("/")) > 1 and path.split("/")[1] in visible_ids)
        return ScopeManifest(
            version=self.version,
            # Never expose the host's absolute DeerMem storage path.
            storage_root=".",
            buckets=visible_buckets,
            facts=visible_facts,
            skipped_paths=visible_skipped,
        )


def file_checksum(path: Path) -> tuple[str, int]:
    """Return ``(sha256_hex, size_bytes)`` without loading the whole file."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def read_fact_document(root: Path | str, relative_path: str) -> dict[str, Any]:
    """Parse one fact file into ``{category, title, content}``.

    Never raises: a fact that cannot be parsed still needs to appear in a
    review queue (labeled ``other``, so it defaults to staying private) rather
    than breaking the whole page.
    """
    empty = {"category": "other", "title": "", "content": ""}
    try:
        text = (Path(root) / relative_path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return empty
    if not text.startswith("---\n") or "\n---\n" not in text:
        return empty
    front, body = text[4:].split("\n---\n", 1)
    try:
        metadata = yaml.safe_load(front)
    except yaml.YAMLError:
        metadata = None
    if not isinstance(metadata, dict):
        metadata = {}

    lines = body.lstrip("\n").splitlines()
    title = ""
    if lines and lines[0].startswith("# "):
        title = lines.pop(0)[2:].strip()
        if lines and not lines[0].strip():
            lines.pop(0)
    category = metadata.get("category")
    return {
        "category": category if isinstance(category, str) and category else "other",
        "title": title,
        "content": "\n".join(lines).rstrip("\n"),
    }


def _scan_bucket(
    root: Path,
    bucket_dir: Path,
) -> tuple[list[FactEntry], list[str], list[str]]:
    """Collect every fact file below one bucket directory."""
    facts: list[FactEntry] = []
    skipped: list[str] = []
    agents: list[str] = []
    agents_root = bucket_dir / "agents"
    if not agents_root.is_dir():
        return facts, skipped, agents

    for agent_dir in sorted(agents_root.iterdir(), key=lambda item: item.name):
        if agent_dir.is_symlink():
            skipped.append(_relative(root, agent_dir))
            continue
        if not agent_dir.is_dir():
            continue
        agents.append(agent_dir.name)
        facts_root = agent_dir / "facts"
        if facts_root.is_symlink():
            skipped.append(_relative(root, facts_root))
            continue
        if not facts_root.is_dir():
            continue
        for candidate in sorted(facts_root.rglob("*"), key=lambda item: item.as_posix()):
            if candidate.is_symlink():
                skipped.append(_relative(root, candidate))
                continue
            if not candidate.is_file():
                continue
            if candidate.suffix != _FACT_SUFFIX:
                # Reported rather than dropped: an unexpected file means the
                # migration's coverage claim would otherwise be overstated.
                skipped.append(_relative(root, candidate))
                continue
            try:
                checksum, size = file_checksum(candidate)
            except OSError:
                logger.warning("Unreadable memory fact skipped: %s", candidate, exc_info=True)
                skipped.append(_relative(root, candidate))
                continue
            facts.append(
                FactEntry(
                    bucket_id=bucket_dir.name,
                    agent_name=agent_dir.name,
                    fact_id=candidate.stem,
                    relative_path=_relative(root, candidate),
                    sha256=checksum,
                    size_bytes=size,
                )
            )
    return facts, skipped, agents


def build_manifest(
    storage_root: Path | str,
    authorities: Iterable[ProjectAuthority],
    *,
    known_user_ids: Iterable[str] = (),
) -> ScopeManifest:
    """Inventory ``{storage_root}/users/*`` into a deterministic manifest."""
    root = Path(storage_root)
    index = build_bucket_index(authorities, known_user_ids=known_user_ids)
    users_root = root / "users"
    if not users_root.is_dir():
        return ScopeManifest(MANIFEST_VERSION, str(root), (), (), ())

    buckets: list[BucketEntry] = []
    all_facts: list[FactEntry] = []
    all_skipped: list[str] = []

    for bucket_dir in sorted(users_root.iterdir(), key=lambda item: item.name):
        if bucket_dir.is_symlink():
            all_skipped.append(_relative(root, bucket_dir))
            continue
        if not bucket_dir.is_dir():
            continue
        facts, skipped, agents = _scan_bucket(root, bucket_dir)
        identity = classify_bucket(bucket_dir.name, index)
        scope = identity.scope
        buckets.append(
            BucketEntry(
                bucket_id=bucket_dir.name,
                classification=identity.classification,
                user_id=scope.user_id if scope else None,
                project_id=scope.project_id if scope else None,
                agent_names=tuple(agents),
                fact_count=len(facts),
                reason=identity.reason,
            )
        )
        all_facts.extend(facts)
        all_skipped.extend(skipped)

    all_facts.sort(key=lambda entry: (entry.bucket_id, entry.agent_name, entry.fact_id))
    return ScopeManifest(
        version=MANIFEST_VERSION,
        storage_root=str(root),
        buckets=tuple(buckets),
        facts=tuple(all_facts),
        skipped_paths=tuple(sorted(all_skipped)),
    )
