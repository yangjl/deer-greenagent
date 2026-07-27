"""Canonical memory scope objects.

Phase 2 gives DeerFlow a vocabulary for *where a memory belongs* that is
independent of where DeerMem happens to store it. Five kinds cover the
product's contexts:

``personal``        a user's own memory, unattached to any project;
``agent``           a user's memory for one named custom agent;
``project``         a user's **private** memory inside one project;
``shared_project``  memory a human explicitly approved for the whole project;
``publication``     governed claims explicitly published to one project.

The two project-wide kinds deliberately carry no ``user_id``: a shared fact
belongs to the project, not to whoever happened to approve it. That is what
lets a second authorized member retrieve it.

Storage placement is not decided here — see ``adapter.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

# Mirrors DeerMem's own ``AGENT_NAME_PATTERN``. Duplicated rather than imported
# so this module stays a pure value layer with no storage-backend dependency.
_AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


class ScopeKind(StrEnum):
    """The canonical memory contexts."""

    PERSONAL = "personal"
    AGENT = "agent"
    PROJECT = "project"
    SHARED_PROJECT = "shared_project"
    PUBLICATION = "publication"


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """An immutable description of one memory context.

    Frozen and value-comparable because the migration journal keys entries by
    scope: a mutable or identity-compared scope would break idempotency.
    """

    kind: ScopeKind
    user_id: str | None = None
    project_id: str | None = None
    agent_name: str | None = None

    @property
    def label(self) -> str:
        """A non-secret label safe to store on hidden snapshot messages."""
        if self.kind is ScopeKind.PERSONAL:
            return "user"
        if self.kind is ScopeKind.AGENT:
            return f"agent:{self.agent_name}"
        if self.kind is ScopeKind.PROJECT:
            return f"project:{self.project_id}"
        if self.kind is ScopeKind.SHARED_PROJECT:
            return f"project-shared:{self.project_id}"
        return f"project-published:{self.project_id}"

    @property
    def is_project_wide(self) -> bool:
        """True when the scope is owned by a project rather than by a user."""
        return self.kind in {ScopeKind.SHARED_PROJECT, ScopeKind.PUBLICATION}


def _require(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string.")
    return value.strip()


def _canonical_agent_name(name: str) -> str:
    """Lowercase and validate an agent name.

    DeerFlow treats agent identifiers as case-insensitive. Canonicalizing at
    the scope boundary means two spellings can never address two buckets.
    """
    canonical = _require(name, field="agent_name").lower()
    if not _AGENT_NAME_PATTERN.match(canonical):
        raise ValueError(f"Invalid agent name {name!r}: names must match {_AGENT_NAME_PATTERN.pattern}")
    return canonical


def personal_scope(user_id: str) -> MemoryScope:
    """A user's project-independent memory."""
    return MemoryScope(kind=ScopeKind.PERSONAL, user_id=_require(user_id, field="user_id"))


def agent_scope(user_id: str, agent_name: str) -> MemoryScope:
    """A user's memory for one named custom agent."""
    return MemoryScope(
        kind=ScopeKind.AGENT,
        user_id=_require(user_id, field="user_id"),
        agent_name=_canonical_agent_name(agent_name),
    )


def project_scope(user_id: str, project_id: str, *, agent_name: str | None = None) -> MemoryScope:
    """A user's *private* memory inside one project (today's live behavior)."""
    return MemoryScope(
        kind=ScopeKind.PROJECT,
        user_id=_require(user_id, field="user_id"),
        project_id=_require(project_id, field="project_id"),
        agent_name=_canonical_agent_name(agent_name) if agent_name is not None else None,
    )


def shared_project_scope(project_id: str, *, agent_name: str | None = None) -> MemoryScope:
    """Memory a human approved for every authorized member of one project."""
    return MemoryScope(
        kind=ScopeKind.SHARED_PROJECT,
        project_id=_require(project_id, field="project_id"),
        agent_name=_canonical_agent_name(agent_name) if agent_name is not None else None,
    )


def publication_scope(project_id: str, *, agent_name: str | None = None) -> MemoryScope:
    """Governed knowledge explicitly published into one target project."""
    return MemoryScope(
        kind=ScopeKind.PUBLICATION,
        project_id=_require(project_id, field="project_id"),
        agent_name=_canonical_agent_name(agent_name) if agent_name is not None else None,
    )
