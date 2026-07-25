"""Resolve the persistent-memory bucket for a runtime scope.

Unfiled conversations retain DeerFlow's existing per-user memory. Project
conversations derive a stable child bucket from the authenticated user and the
durable project id, preventing facts from one project from leaking into
another while preserving per-agent memory inside each bucket.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from deerflow.runtime.user_context import resolve_runtime_user_id

_PROJECT_BUCKET_SEPARATOR = "--project--"
_PROJECT_DIGEST_LENGTH = 24


def _project_scope_value(context: object) -> str | None:
    if not isinstance(context, Mapping):
        return None
    project_id = context.get("project_id")
    if isinstance(project_id, str) and project_id:
        return project_id
    project_root = context.get("project_root")
    if isinstance(project_root, str) and project_root:
        # ``project_id`` is normally always present. The root fallback stays
        # isolated rather than silently dropping into user-global memory while
        # durable project metadata is temporarily unavailable.
        return f"root:{project_root}"
    return None


def memory_scope_label(context: object) -> str:
    """Return a non-secret label stored on hidden memory snapshot messages."""
    project_scope = _project_scope_value(context)
    if project_scope is None:
        return "user"
    return f"project:{project_scope}"


def scoped_memory_user_id(user_id: str, context: object) -> str:
    """Return the backend bucket id for *user_id* in *context*.

    The hash keeps storage path components bounded and filesystem-safe. The
    authenticated user id remains part of the bucket, so two users opening the
    same project id cannot share memory.
    """
    project_scope = _project_scope_value(context)
    if project_scope is None:
        return user_id
    digest = hashlib.sha256(project_scope.encode("utf-8")).hexdigest()[:_PROJECT_DIGEST_LENGTH]
    return f"{user_id}{_PROJECT_BUCKET_SEPARATOR}{digest}"


def resolve_scoped_memory_user_id(runtime: object | None) -> str:
    """Resolve authenticated user + durable project scope from *runtime*."""
    context = getattr(runtime, "context", None)
    return scoped_memory_user_id(resolve_runtime_user_id(runtime), context)
