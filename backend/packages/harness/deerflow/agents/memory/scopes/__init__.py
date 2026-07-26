"""Canonical memory scopes and the DeerMem compatibility bridge (Phase 2).

Import from this package rather than the submodules: the split into
``models`` / ``adapter`` / ``inventory`` / ``classify`` / ``migration`` is an
implementation detail, and only the names re-exported here are contractual.
"""

from deerflow.agents.memory.scopes.adapter import (
    PROJECT_SEPARATOR,
    PUBLICATION_SEPARATOR,
    RESERVED_SEPARATORS,
    ReservedScopeIdentifierError,
    StorageBinding,
    bind_scope,
    project_bucket_id,
    project_digest,
    publication_bucket_id,
    shared_bucket_id,
)
from deerflow.agents.memory.scopes.models import (
    MemoryScope,
    ScopeKind,
    agent_scope,
    personal_scope,
    project_scope,
    publication_scope,
    shared_project_scope,
)

__all__ = [
    "PROJECT_SEPARATOR",
    "PUBLICATION_SEPARATOR",
    "RESERVED_SEPARATORS",
    "MemoryScope",
    "ReservedScopeIdentifierError",
    "ScopeKind",
    "StorageBinding",
    "agent_scope",
    "bind_scope",
    "personal_scope",
    "project_bucket_id",
    "project_digest",
    "project_scope",
    "publication_bucket_id",
    "publication_scope",
    "shared_bucket_id",
    "shared_project_scope",
]
