"""Phase 2: the compatibility adapter onto DeerMem's ``(user_id, agent_name)``.

DeerMem stores against exactly two keys. The adapter is what lets four
canonical scopes ride on that boundary without moving a single existing byte,
so the most important property is that the project binding is *bit-identical*
to what ``scoped_memory_user_id`` already produces today.
"""

from __future__ import annotations

import pytest

from deerflow.agents.memory.backends.deermem.deermem.core.paths import (
    DEFAULT_AGENT_BUCKET,
    safe_user_id,
)
from deerflow.agents.memory.scope import scoped_memory_user_id
from deerflow.agents.memory.scopes import (
    MemoryScope,
    ReservedScopeIdentifierError,
    ScopeKind,
    agent_scope,
    bind_scope,
    personal_scope,
    project_scope,
    publication_scope,
    shared_project_scope,
)


def test_project_binding_is_byte_identical_to_live_behavior() -> None:
    """The no-op guarantee: existing project buckets keep their exact path."""
    live = scoped_memory_user_id("user-1", {"project_id": "project-a"})
    assert bind_scope(project_scope("user-1", "project-a")).user_id == live


def test_personal_binding_is_the_bare_user_id() -> None:
    binding = bind_scope(personal_scope("user-1"))
    assert binding.user_id == "user-1"
    assert binding.agent_name == DEFAULT_AGENT_BUCKET


def test_agent_binding_keeps_the_user_bucket_and_selects_the_agent() -> None:
    binding = bind_scope(agent_scope("user-1", "breeding-bot"))
    assert binding.user_id == "user-1"
    assert binding.agent_name == "breeding-bot"


def test_shared_and_publication_buckets_are_unreachable_by_any_real_user() -> None:
    """Structural, not conventional: their user component is empty.

    ``safe_user_id`` rejects an empty user id, so no authenticated identity can
    ever be normalized into a bucket name that starts with the separator.
    """
    shared = bind_scope(shared_project_scope("project-a")).user_id
    published = bind_scope(publication_scope("project-a")).user_id

    assert shared.startswith("--project--")
    assert published.startswith("--published--")
    assert shared != published
    with pytest.raises(ValueError):
        safe_user_id("")


def test_shared_bucket_is_stable_and_project_specific() -> None:
    a = bind_scope(shared_project_scope("project-a")).user_id
    b = bind_scope(shared_project_scope("project-b")).user_id
    assert a == bind_scope(shared_project_scope("project-a")).user_id
    assert a != b


def test_bucket_names_survive_deermem_path_sanitization_unchanged() -> None:
    """A bucket that gets rewritten by ``safe_user_id`` would split storage."""
    for scope in (
        project_scope("user-1", "project-a"),
        shared_project_scope("project-a"),
        publication_scope("project-a"),
    ):
        bucket = bind_scope(scope).user_id
        assert safe_user_id(bucket) == bucket


@pytest.mark.parametrize(
    "hostile_user_id",
    [
        "--project--0123456789abcdef01234567",
        "victim--project--0123456789abcdef01234567",
        "--published--0123456789abcdef01234567",
        "attacker--published--deadbeef",
    ],
)
def test_reserved_separators_are_refused_in_a_user_identity(hostile_user_id: str) -> None:
    """A crafted user id must not be able to address a project bucket.

    Every character of the separators is inside ``safe_user_id``'s charset, so
    without this guard an identity could name someone else's project bucket —
    including the private project buckets that already exist today.
    """
    for scope_factory in (
        lambda: personal_scope(hostile_user_id),
        lambda: agent_scope(hostile_user_id, "bot"),
        lambda: project_scope(hostile_user_id, "project-a"),
    ):
        with pytest.raises(ReservedScopeIdentifierError):
            bind_scope(scope_factory())


def test_binding_is_hashable_for_dedupe() -> None:
    first = bind_scope(project_scope("user-1", "project-a"))
    assert first == bind_scope(project_scope("user-1", "project-a"))
    assert len({first, bind_scope(project_scope("user-1", "project-a"))}) == 1


@pytest.mark.parametrize(
    "scope",
    [
        MemoryScope(kind=ScopeKind.PERSONAL),
        MemoryScope(kind=ScopeKind.AGENT, user_id="user-1"),
        MemoryScope(kind=ScopeKind.PROJECT, user_id="user-1"),
        MemoryScope(kind=ScopeKind.SHARED_PROJECT),
        MemoryScope(kind=ScopeKind.PUBLICATION, project_id="project-a", user_id="user-1"),
    ],
)
def test_binding_rejects_internally_inconsistent_scope_objects(scope: MemoryScope) -> None:
    with pytest.raises(ValueError):
        bind_scope(scope)
