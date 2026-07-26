"""Recompute legacy DeerMem bucket names back into canonical scopes.

A bucket name embeds ``sha256(project_scope)[:24]``, so it cannot be *read*
back into a project id. It can only be *recomputed*: derive every bucket name
an authoritative ``(user_id, project_id)`` pair would produce and match by
name.

Nothing is guessed. A bucket claimed by zero authorities, or by more than one,
is quarantined with a reason — a wrong mapping would attach one person's
private facts to another project.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from deerflow.agents.memory.backends.deermem.deermem.core.paths import safe_user_id
from deerflow.agents.memory.scope import scoped_memory_user_id
from deerflow.agents.memory.scopes.models import (
    MemoryScope,
    personal_scope,
    project_scope,
    publication_scope,
    shared_project_scope,
)


class BucketClassification(StrEnum):
    """Operator-facing classification of one on-disk bucket."""

    PERSONAL = "personal"
    PROJECT = "project"
    SHARED_PROJECT = "shared_project"
    PUBLICATION = "publication"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class ProjectAuthority:
    """One authoritative membership record: this user is in this project.

    ``project_root`` mirrors the durable ``projects.root_path`` and is what
    lets the root-fallback buckets (written when ``project_id`` was briefly
    unavailable) be recognized instead of quarantined.
    """

    user_id: str
    project_id: str
    project_root: str | None = None


@dataclass(frozen=True, slots=True)
class BucketIdentity:
    """What a single bucket name resolves to, if exactly one thing does."""

    classification: BucketClassification
    scope: MemoryScope | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class BucketIndex:
    """Reverse index from on-disk bucket name to its recomputed identity."""

    entries: dict[str, BucketIdentity] = field(default_factory=dict)


def _claim(
    claims: dict[str, list[BucketIdentity]],
    bucket_id: str,
    identity: BucketIdentity,
) -> None:
    claims.setdefault(safe_user_id(bucket_id), []).append(identity)


def build_bucket_index(
    authorities: Iterable[ProjectAuthority],
    *,
    known_user_ids: Iterable[str] = (),
) -> BucketIndex:
    """Derive the bucket name every authoritative pair would have produced.

    Bucket names are passed through ``safe_user_id`` because that is what the
    storage layer wrote to disk — a raw identity like an email address never
    appears as a directory name.
    """
    claims: dict[str, list[BucketIdentity]] = {}
    projects: dict[str, None] = {}

    for user_id in known_user_ids:
        _claim(claims, user_id, BucketIdentity(BucketClassification.PERSONAL, personal_scope(user_id)))

    for authority in authorities:
        projects[authority.project_id] = None
        scope = project_scope(authority.user_id, authority.project_id)
        _claim(
            claims,
            scoped_memory_user_id(authority.user_id, {"project_id": authority.project_id}),
            BucketIdentity(BucketClassification.PROJECT, scope),
        )
        if authority.project_root:
            # The live fallback digests ``root:<path>`` when a durable project
            # id was momentarily unavailable; those buckets are real.
            _claim(
                claims,
                scoped_memory_user_id(authority.user_id, {"project_root": authority.project_root}),
                BucketIdentity(BucketClassification.PROJECT, scope),
            )

    for project_id in projects:
        from deerflow.agents.memory.scopes.adapter import publication_bucket_id, shared_bucket_id

        _claim(
            claims,
            shared_bucket_id(project_id),
            BucketIdentity(BucketClassification.SHARED_PROJECT, shared_project_scope(project_id)),
        )
        _claim(
            claims,
            publication_bucket_id(project_id),
            BucketIdentity(BucketClassification.PUBLICATION, publication_scope(project_id)),
        )

    entries: dict[str, BucketIdentity] = {}
    for bucket_id, identities in claims.items():
        distinct = {(item.classification, item.scope) for item in identities}
        if len(distinct) == 1:
            entries[bucket_id] = identities[0]
            continue
        entries[bucket_id] = BucketIdentity(
            BucketClassification.QUARANTINED,
            None,
            reason=(f"Ambiguous bucket: {len(distinct)} authoritative scopes recompute to the same name."),
        )
    return BucketIndex(entries=entries)


def classify_bucket(bucket_id: str, index: BucketIndex) -> BucketIdentity:
    """Classify one on-disk bucket name against a recomputed *index*."""
    identity = index.entries.get(bucket_id)
    if identity is not None:
        return identity
    return BucketIdentity(
        BucketClassification.QUARANTINED,
        None,
        reason="No authoritative user or project recomputes to this bucket name.",
    )
