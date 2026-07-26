"""Phase 2: retrieval must span private + shared without crossing projects.

The Phase 2 no-go is "project selection can expose a previous project's memory
snapshot", and the human exit review is "two authorized users retrieve the same
approved project fact while all unapproved facts remain private". Both are
properties of the bucket *chain* a run reads, so they are tested here.
"""

from __future__ import annotations

from deerflow.agents.memory.scope import scoped_memory_user_id
from deerflow.agents.memory.scopes import bind_scope, shared_project_scope
from deerflow.agents.memory.scopes.reader import scoped_memory_bucket_chain


def test_unfiled_conversations_read_only_the_user_bucket() -> None:
    """No project, no shared bucket — projectless chat is unchanged."""
    assert scoped_memory_bucket_chain("user-1", {}) == ("user-1",)
    assert scoped_memory_bucket_chain("user-1", None) == ("user-1",)


def test_project_runs_read_private_first_then_shared() -> None:
    chain = scoped_memory_bucket_chain("user-1", {"project_id": "project-a"})

    assert chain == (
        scoped_memory_user_id("user-1", {"project_id": "project-a"}),
        bind_scope(shared_project_scope("project-a")).user_id,
    )


def test_two_members_of_one_project_share_exactly_one_bucket() -> None:
    """The approved fact is reachable by both; the private ones are not."""
    first = scoped_memory_bucket_chain("user-1", {"project_id": "project-a"})
    second = scoped_memory_bucket_chain("user-2", {"project_id": "project-a"})

    assert set(first) & set(second) == {bind_scope(shared_project_scope("project-a")).user_id}
    assert first[0] != second[0]


def test_no_bucket_is_shared_across_two_projects() -> None:
    """The Phase 2 no-go, stated directly."""
    a = scoped_memory_bucket_chain("user-1", {"project_id": "project-a"})
    b = scoped_memory_bucket_chain("user-1", {"project_id": "project-b"})

    assert set(a).isdisjoint(set(b))


def test_a_project_run_never_reads_the_user_global_bucket() -> None:
    """Otherwise switching into a project would leak the previous snapshot."""
    chain = scoped_memory_bucket_chain("user-1", {"project_id": "project-a"})

    assert "user-1" not in chain


def test_root_fallback_shares_a_bucket_with_the_same_root() -> None:
    """The fallback digests ``root:<path>``; shared must digest the same value."""
    context = {"project_root": "/home/u1/projects/B"}
    first = scoped_memory_bucket_chain("user-1", context)
    second = scoped_memory_bucket_chain("user-2", context)

    assert first[0] != second[0]
    assert first[1] == second[1]
    assert first[1].startswith("--project--")


def test_chain_is_deterministic() -> None:
    context = {"project_id": "project-a"}
    assert scoped_memory_bucket_chain("user-1", context) == scoped_memory_bucket_chain("user-1", context)


def test_writes_target_only_the_private_head_of_the_chain() -> None:
    """Nothing may auto-write into shared memory; sharing is a human act."""
    from deerflow.agents.memory.scopes.reader import memory_write_bucket

    context = {"project_id": "project-a"}
    assert memory_write_bucket("user-1", context) == scoped_memory_bucket_chain("user-1", context)[0]
    assert memory_write_bucket("user-1", context) != bind_scope(shared_project_scope("project-a")).user_id
    assert memory_write_bucket("user-1", {}) == "user-1"
