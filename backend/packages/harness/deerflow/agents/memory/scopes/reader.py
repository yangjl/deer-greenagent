"""Which buckets a run reads from, and which single bucket it writes to.

Retrieval spans two buckets for a project conversation — the member's own
private project memory, then the project's shared memory. Writing does not:
memory learned during a run always lands in the private head of the chain, and
a fact only ever reaches the shared bucket through an explicit human decision
in the migration review (see ``migration.py``).

Nothing here reads the user-global bucket during a project run. That is the
Phase 2 no-go — a project conversation must not surface the snapshot from
whatever the user was doing before.
"""

from __future__ import annotations

from deerflow.agents.memory.scope import project_scope_value, scoped_memory_user_id
from deerflow.agents.memory.scopes.adapter import PROJECT_SEPARATOR, project_digest
from deerflow.runtime.user_context import resolve_runtime_user_id


def shared_bucket_for_scope_value(scope_value: str) -> str:
    """Shared bucket for the exact value a private bucket was derived from."""
    return f"{PROJECT_SEPARATOR}{project_digest(scope_value)}"


def scoped_memory_bucket_chain(user_id: str, context: object) -> tuple[str, ...]:
    """Return the buckets *user_id* may read in *context*, private first."""
    scope_value = project_scope_value(context)
    if scope_value is None:
        return (user_id,)
    return (
        scoped_memory_user_id(user_id, context),
        shared_bucket_for_scope_value(scope_value),
    )


def memory_write_bucket(user_id: str, context: object) -> str:
    """Return the single bucket a run may learn into."""
    return scoped_memory_bucket_chain(user_id, context)[0]


def resolve_memory_bucket_chain(runtime: object | None) -> tuple[str, ...]:
    """Resolve the read chain from an agent runtime."""
    context = getattr(runtime, "context", None)
    return scoped_memory_bucket_chain(resolve_runtime_user_id(runtime), context)


def shared_memory_buckets(runtime: object | None) -> tuple[str, ...]:
    """Project-wide buckets only — the tail of the read chain."""
    return resolve_memory_bucket_chain(runtime)[1:]
