"""Phase 2: checksum-preserving inventory of legacy DeerMem buckets.

The manifest is the evidence artifact an operator downloads. It must be
deterministic (so two runs are diffable), checksum-preserving (so a later
migration can prove it moved the exact bytes it inventoried), and free of
fact *content* — the landing view shows counts, not other people's memories.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from deerflow.agents.memory.scope import scoped_memory_user_id
from deerflow.agents.memory.scopes.classify import BucketClassification, ProjectAuthority
from deerflow.agents.memory.scopes.inventory import build_manifest

FACT_BODY = "---\nid: fact_1\ncategory: preference\n---\n\n# Prefers CIMMYT lines\n\nUses CIMMYT tropical lines.\n"


def _write_fact(root: Path, bucket: str, agent: str, fact_id: str, body: str = FACT_BODY) -> Path:
    prefix = hashlib.sha256(fact_id.encode("utf-8")).hexdigest()[:2]
    path = root / "users" / bucket / "agents" / agent / "facts" / prefix / f"{fact_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _authorities() -> tuple[ProjectAuthority, ...]:
    return (ProjectAuthority(user_id="user-1", project_id="project-a"),)


def test_manifest_records_a_checksum_for_every_fact(tmp_path: Path) -> None:
    bucket = scoped_memory_user_id("user-1", {"project_id": "project-a"})
    path = _write_fact(tmp_path, bucket, "__default__", "fact_1")

    manifest = build_manifest(tmp_path, _authorities(), known_user_ids=("user-1",))

    assert len(manifest.facts) == 1
    entry = manifest.facts[0]
    assert entry.fact_id == "fact_1"
    assert entry.bucket_id == bucket
    assert entry.agent_name == "__default__"
    assert entry.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert entry.size_bytes == len(path.read_bytes())


def test_manifest_never_carries_fact_content(tmp_path: Path) -> None:
    """Counts and checksums are safe to share; bodies are not."""
    bucket = scoped_memory_user_id("user-1", {"project_id": "project-a"})
    _write_fact(tmp_path, bucket, "__default__", "fact_1")

    serialized = json.dumps(build_manifest(tmp_path, _authorities(), known_user_ids=("user-1",)).to_dict())

    assert "CIMMYT" not in serialized
    assert "Uses CIMMYT tropical lines" not in serialized


def test_buckets_are_classified_and_unknown_ones_quarantined(tmp_path: Path) -> None:
    known = scoped_memory_user_id("user-1", {"project_id": "project-a"})
    _write_fact(tmp_path, known, "__default__", "fact_1")
    _write_fact(tmp_path, "user-1", "__default__", "fact_2")
    _write_fact(tmp_path, "ghost--project--" + "0" * 24, "__default__", "fact_3")

    manifest = build_manifest(tmp_path, _authorities(), known_user_ids=("user-1",))
    by_id = {bucket.bucket_id: bucket for bucket in manifest.buckets}

    assert by_id[known].classification is BucketClassification.PROJECT
    assert by_id[known].project_id == "project-a"
    assert by_id["user-1"].classification is BucketClassification.PERSONAL
    quarantined = by_id["ghost--project--" + "0" * 24]
    assert quarantined.classification is BucketClassification.QUARANTINED
    assert quarantined.reason


def test_manifest_is_deterministic_and_ordered(tmp_path: Path) -> None:
    bucket = scoped_memory_user_id("user-1", {"project_id": "project-a"})
    for fact_id in ("fact_c", "fact_a", "fact_b"):
        _write_fact(tmp_path, bucket, "__default__", fact_id)

    first = build_manifest(tmp_path, _authorities(), known_user_ids=("user-1",))
    second = build_manifest(tmp_path, _authorities(), known_user_ids=("user-1",))

    assert first.to_dict() == second.to_dict()
    assert [entry.fact_id for entry in first.facts] == ["fact_a", "fact_b", "fact_c"]


def test_custom_agent_facts_are_preserved_as_their_own_scope(tmp_path: Path) -> None:
    """A custom agent's facts must not be flattened into the default bucket."""
    _write_fact(tmp_path, "user-1", "__default__", "fact_1")
    _write_fact(tmp_path, "user-1", "breeding-bot", "fact_2")

    manifest = build_manifest(tmp_path, _authorities(), known_user_ids=("user-1",))

    agents = {entry.agent_name for entry in manifest.facts}
    assert agents == {"__default__", "breeding-bot"}
    assert {entry.fact_count for entry in manifest.buckets if entry.bucket_id == "user-1"} == {2}


def test_missing_storage_root_yields_an_empty_manifest(tmp_path: Path) -> None:
    manifest = build_manifest(tmp_path / "nope", _authorities(), known_user_ids=("user-1",))

    assert manifest.buckets == ()
    assert manifest.facts == ()
    assert manifest.to_dict()["version"] >= 1


def test_unreadable_fact_file_is_reported_not_silently_dropped(tmp_path: Path) -> None:
    """Silently skipping a file would understate what a migration must cover."""
    bucket = scoped_memory_user_id("user-1", {"project_id": "project-a"})
    _write_fact(tmp_path, bucket, "__default__", "fact_1")
    stray = tmp_path / "users" / bucket / "agents" / "__default__" / "facts" / "ab"
    stray.mkdir(parents=True, exist_ok=True)
    (stray / "not-a-fact.txt").write_text("junk", encoding="utf-8")

    manifest = build_manifest(tmp_path, _authorities(), known_user_ids=("user-1",))

    assert len(manifest.facts) == 1
    assert manifest.skipped_paths == (f"users/{bucket}/agents/__default__/facts/ab/not-a-fact.txt",)


def test_manifest_never_follows_a_fact_symlink_outside_storage(tmp_path: Path) -> None:
    bucket = scoped_memory_user_id("user-1", {"project_id": "project-a"})
    facts = tmp_path / "users" / bucket / "agents" / "__default__" / "facts" / "ab"
    facts.mkdir(parents=True)
    outside = tmp_path.parent / f"{tmp_path.name}-private-memory.md"
    outside.write_text("private data outside the DeerMem root", encoding="utf-8")
    link = facts / "fact_link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    manifest = build_manifest(tmp_path, _authorities(), known_user_ids=("user-1",))

    assert manifest.facts == ()
    assert manifest.skipped_paths == (f"users/{bucket}/agents/__default__/facts/ab/fact_link.md",)
