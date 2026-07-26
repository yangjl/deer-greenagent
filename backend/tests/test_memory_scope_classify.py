"""Phase 2: recompute legacy bucket mappings from authoritative pairs.

Bucket names embed a one-way digest, so a legacy bucket cannot be *read* back
into a project id. It can only be *recomputed*: derive every bucket an
authoritative (user, project) pair would produce and match by name. Anything
that does not match exactly one authority is quarantined, never guessed.
"""

from __future__ import annotations

from deerflow.agents.memory.backends.deermem.deermem.core.paths import safe_user_id
from deerflow.agents.memory.scope import scoped_memory_user_id
from deerflow.agents.memory.scopes import ScopeKind, project_digest
from deerflow.agents.memory.scopes.classify import (
    BucketClassification,
    ProjectAuthority,
    build_bucket_index,
    classify_bucket,
)


def _authority() -> tuple[ProjectAuthority, ...]:
    return (
        ProjectAuthority(user_id="user-1", project_id="project-a"),
        ProjectAuthority(user_id="user-2", project_id="project-a"),
        ProjectAuthority(user_id="user-1", project_id="project-b", project_root="/home/u1/projects/B"),
    )


def test_recognized_legacy_project_bucket_is_mapped_back_to_its_pair() -> None:
    index = build_bucket_index(_authority())
    legacy = scoped_memory_user_id("user-1", {"project_id": "project-a"})

    result = classify_bucket(legacy, index)

    assert result.classification is BucketClassification.PROJECT
    assert result.scope is not None
    assert result.scope.kind is ScopeKind.PROJECT
    assert result.scope.user_id == "user-1"
    assert result.scope.project_id == "project-a"


def test_root_fallback_buckets_are_recognized_too() -> None:
    """``scoped_memory_user_id`` digests ``root:<path>`` when no project id.

    Those buckets exist on disk in the wild; without recomputing the same
    fallback they would all be quarantined as unknown.
    """
    index = build_bucket_index(_authority())
    legacy = scoped_memory_user_id("user-1", {"project_root": "/home/u1/projects/B"})

    result = classify_bucket(legacy, index)

    assert result.classification is BucketClassification.PROJECT
    assert result.scope is not None
    assert result.scope.project_id == "project-b"


def test_personal_bucket_is_recognized_by_sanitized_identity() -> None:
    """Buckets on disk are ``safe_user_id`` output, not the raw identity."""
    index = build_bucket_index(_authority(), known_user_ids=("user-1", "person@example.com"))

    assert classify_bucket("user-1", index).classification is BucketClassification.PERSONAL
    sanitized = safe_user_id("person@example.com")
    assert sanitized != "person@example.com"
    result = classify_bucket(sanitized, index)
    assert result.classification is BucketClassification.PERSONAL
    assert result.scope is not None
    assert result.scope.user_id == "person@example.com"


def test_project_wide_buckets_are_recognized_and_not_quarantined() -> None:
    index = build_bucket_index(_authority())

    shared = classify_bucket(f"--project--{project_digest('project-a')}", index)
    published = classify_bucket(f"--published--{project_digest('project-a')}", index)

    assert shared.classification is BucketClassification.SHARED_PROJECT
    assert published.classification is BucketClassification.PUBLICATION


def test_unknown_bucket_is_quarantined_with_a_reason() -> None:
    index = build_bucket_index(_authority())

    orphan = classify_bucket("user-99--project--" + "0" * 24, index)

    assert orphan.classification is BucketClassification.QUARANTINED
    assert orphan.scope is None
    assert orphan.reason


def test_a_stray_bucket_that_is_not_a_known_user_is_quarantined_not_assumed_personal() -> None:
    """A bare directory name is only 'personal' if an authority claims it."""
    index = build_bucket_index(_authority(), known_user_ids=("user-1",))

    assert classify_bucket("user-1", index).classification is BucketClassification.PERSONAL
    assert classify_bucket("someone-else", index).classification is BucketClassification.QUARANTINED


def test_ambiguous_bucket_claimed_by_two_authorities_is_quarantined() -> None:
    """Two claimants means we cannot know the owner, so we refuse to pick."""
    collided = "user-1--project--" + project_digest("project-a")
    index = build_bucket_index(
        (ProjectAuthority(user_id="user-1", project_id="project-a"),),
        known_user_ids=("user-1", collided),
    )

    result = classify_bucket(collided, index)

    assert result.classification is BucketClassification.QUARANTINED
    assert "ambiguous" in result.reason.lower()


def test_classification_is_deterministic_across_repeated_index_builds() -> None:
    first = build_bucket_index(_authority(), known_user_ids=("user-1",))
    second = build_bucket_index(tuple(reversed(_authority())), known_user_ids=("user-1",))
    legacy = scoped_memory_user_id("user-2", {"project_id": "project-a"})

    assert classify_bucket(legacy, first) == classify_bucket(legacy, second)
