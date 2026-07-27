"""Bind a canonical :class:`MemoryScope` onto DeerMem's storage boundary.

DeerMem stores against exactly two keys, ``(user_id, agent_name)``. This module
expresses five canonical scopes on those two keys **without moving a byte of
existing data**: the ``project`` binding is bit-identical to what
``deerflow.agents.memory.scope.scoped_memory_user_id`` already produces, so
every live per-user project bucket keeps its current path.

The project-wide buckets are separated structurally rather than by convention.
Their user component is *empty*, and DeerMem's ``safe_user_id`` rejects an
empty user id — so no authenticated identity can normalize into one. The one
remaining hole is a user id that literally contains a separator; scopes
carrying such an identity are refused here rather than silently addressing
someone else's bucket. That guard also protects the per-user project buckets
that already exist today.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from deerflow.agents.memory.backends.deermem.deermem.core.paths import (
    DEFAULT_AGENT_BUCKET,
    validate_agent_name,
)
from deerflow.agents.memory.scopes.models import MemoryScope, ScopeKind

# Kept in lockstep with ``deerflow.agents.memory.scope``; the identity test in
# ``tests/test_memory_scope_adapter.py`` fails if the two ever drift.
PROJECT_SEPARATOR = "--project--"
PUBLICATION_SEPARATOR = "--published--"
PROJECT_DIGEST_LENGTH = 24

RESERVED_SEPARATORS = (PROJECT_SEPARATOR, PUBLICATION_SEPARATOR)


class ReservedScopeIdentifierError(ValueError):
    """A user identity tried to occupy a reserved project-bucket namespace."""


@dataclass(frozen=True, slots=True)
class StorageBinding:
    """The concrete ``(user_id, agent_name)`` DeerMem should be called with."""

    user_id: str
    agent_name: str


def project_digest(project_id: str) -> str:
    """Return the bounded, filesystem-safe digest for a project id."""
    return hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:PROJECT_DIGEST_LENGTH]


def _reject_reserved(user_id: str) -> str:
    for separator in RESERVED_SEPARATORS:
        if separator in user_id:
            raise ReservedScopeIdentifierError(f"User identity may not contain the reserved separator {separator!r}.")
    return user_id


def project_bucket_id(user_id: str, project_id: str) -> str:
    """The private per-user project bucket. Identical to live behavior."""
    return f"{_reject_reserved(user_id)}{PROJECT_SEPARATOR}{project_digest(project_id)}"


def shared_bucket_id(project_id: str) -> str:
    """The project-wide bucket. Empty user component makes it unreachable."""
    return f"{PROJECT_SEPARATOR}{project_digest(project_id)}"


def publication_bucket_id(project_id: str) -> str:
    """The governed-knowledge publication bucket for a target project."""
    return f"{PUBLICATION_SEPARATOR}{project_digest(project_id)}"


def bind_scope(scope: MemoryScope) -> StorageBinding:
    """Resolve *scope* to the DeerMem keys that hold it."""
    if scope.kind is ScopeKind.PERSONAL:
        if not scope.user_id or scope.project_id is not None or scope.agent_name is not None:
            raise ValueError("personal scope requires only user_id.")
    elif scope.kind is ScopeKind.AGENT:
        if not scope.user_id or not scope.agent_name or scope.project_id is not None:
            raise ValueError("agent scope requires user_id and agent_name only.")
    elif scope.kind is ScopeKind.PROJECT:
        if not scope.user_id or not scope.project_id:
            raise ValueError("project scope requires user_id and project_id.")
    elif scope.kind in {ScopeKind.SHARED_PROJECT, ScopeKind.PUBLICATION}:
        if not scope.project_id or scope.user_id is not None:
            raise ValueError(f"{scope.kind.value} scope requires project_id and no user_id.")

    agent_name = scope.agent_name or DEFAULT_AGENT_BUCKET
    validate_agent_name(agent_name)

    if scope.kind in {ScopeKind.PERSONAL, ScopeKind.AGENT}:
        return StorageBinding(user_id=_reject_reserved(scope.user_id), agent_name=agent_name)
    if scope.kind is ScopeKind.PROJECT:
        return StorageBinding(
            user_id=project_bucket_id(scope.user_id, scope.project_id),
            agent_name=agent_name,
        )
    if scope.kind is ScopeKind.SHARED_PROJECT:
        return StorageBinding(user_id=shared_bucket_id(scope.project_id), agent_name=agent_name)
    return StorageBinding(user_id=publication_bucket_id(scope.project_id), agent_name=agent_name)
