"""Phase 2: canonical memory scope objects.

The scope object is the vocabulary the rest of Phase 2 speaks. It must be
immutable, comparable, hashable (it is used as a dict key in the migration
journal), and it must refuse to describe a scope that cannot exist.
"""

from __future__ import annotations

import dataclasses

import pytest

from deerflow.agents.memory.scopes import (
    MemoryScope,
    ScopeKind,
    agent_scope,
    personal_scope,
    project_scope,
    publication_scope,
    shared_project_scope,
)


def test_four_canonical_kinds_exist() -> None:
    assert {kind.value for kind in ScopeKind} == {
        "personal",
        "agent",
        "project",
        "shared_project",
        "publication",
    }


def test_scopes_are_frozen_and_hashable() -> None:
    scope = project_scope("user-1", "project-a")
    assert isinstance(scope, MemoryScope)
    with pytest.raises(dataclasses.FrozenInstanceError):
        scope.user_id = "user-2"  # type: ignore[misc]
    # Used as a journal key, so equality and hashing must be value-based.
    assert scope == project_scope("user-1", "project-a")
    assert len({scope, project_scope("user-1", "project-a")}) == 1
    assert scope != project_scope("user-1", "project-b")


def test_personal_and_agent_scopes_carry_their_dimensions() -> None:
    personal = personal_scope("user-1")
    assert personal.kind is ScopeKind.PERSONAL
    assert personal.user_id == "user-1"
    assert personal.project_id is None
    assert personal.agent_name is None

    agent = agent_scope("user-1", "Breeding-Bot")
    assert agent.kind is ScopeKind.AGENT
    # Agent identifiers are case-insensitive in DeerFlow; canonicalize once here
    # so two spellings can never address two buckets.
    assert agent.agent_name == "breeding-bot"


def test_shared_and_publication_scopes_have_no_user_component() -> None:
    """A shared fact belongs to the project, not to whoever approved it."""
    shared = shared_project_scope("project-a")
    assert shared.kind is ScopeKind.SHARED_PROJECT
    assert shared.project_id == "project-a"
    assert shared.user_id is None

    published = publication_scope("project-a")
    assert published.kind is ScopeKind.PUBLICATION
    assert published.user_id is None


def test_scope_constructors_reject_empty_identifiers() -> None:
    for factory, args in (
        (personal_scope, ("",)),
        (project_scope, ("user-1", "")),
        (project_scope, ("", "project-a")),
        (shared_project_scope, ("",)),
        (publication_scope, ("   ",)),
        (agent_scope, ("user-1", "")),
    ):
        with pytest.raises(ValueError):
            factory(*args)  # type: ignore[operator]


def test_agent_scope_rejects_names_outside_the_path_safe_grammar() -> None:
    for name in ("../escape", "has space", "slash/name", "dot.name"):
        with pytest.raises(ValueError):
            agent_scope("user-1", name)


def test_scope_label_is_stable_and_non_secret() -> None:
    """Labels ride on hidden snapshot messages, so they must not leak content."""
    assert personal_scope("user-1").label == "user"
    assert project_scope("user-1", "project-a").label == "project:project-a"
    assert shared_project_scope("project-a").label == "project-shared:project-a"
    assert publication_scope("project-a").label == "project-published:project-a"
    assert agent_scope("user-1", "bot").label == "agent:bot"
