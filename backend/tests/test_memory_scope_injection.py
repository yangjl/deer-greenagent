"""Phase 2: shared project memory reaches the prompt, labeled as shared.

Retrieval is where "two authorized users see the same approved fact" actually
becomes true. The shared block must be visibly distinct from the member's own
private memory so the model does not present someone else's approved fact as
the current user's private context.
"""

from __future__ import annotations

from typing import Any

import pytest

from deerflow.agents.lead_agent.prompt import _get_memory_context
from deerflow.agents.memory.scopes import bind_scope, shared_project_scope

SHARED_BUCKET = bind_scope(shared_project_scope("project-a")).user_id


class FakeManager:
    def __init__(self, contexts: dict[str, str]) -> None:
        self.contexts = contexts
        self.calls: list[tuple[str, str | None]] = []

    def get_context(self, *, user_id: str, agent_name: str | None = None) -> str:
        self.calls.append((user_id, agent_name))
        return self.contexts.get(user_id, "")


class FakeConfig:
    class memory:  # noqa: N801 - mirrors AppConfig attribute shape
        enabled = True
        injection_enabled = True


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> Any:
    fake = FakeManager({"user-1--project--x": "- private fact", SHARED_BUCKET: "- approved shared fact"})
    monkeypatch.setattr("deerflow.agents.memory.get_memory_manager", lambda: fake)
    return fake


def test_shared_memory_is_injected_and_labeled(manager: FakeManager) -> None:
    block = _get_memory_context(
        None,
        app_config=FakeConfig(),
        user_id="user-1--project--x",
        shared_user_ids=(SHARED_BUCKET,),
    )

    assert "private fact" in block
    assert "approved shared fact" in block
    assert "<project_shared_memory>" in block
    # The shared section must be closed and nested inside the memory block.
    assert block.index("<memory>") < block.index("<project_shared_memory>")
    assert "</project_shared_memory>" in block


def test_private_memory_is_not_labeled_as_shared(manager: FakeManager) -> None:
    block = _get_memory_context(None, app_config=FakeConfig(), user_id="user-1--project--x")

    assert "private fact" in block
    assert "project_shared_memory" not in block
    assert manager.calls == [("user-1--project--x", None)]


def test_shared_only_memory_still_produces_a_block(manager: FakeManager) -> None:
    """A member with no private facts must still see the project's shared ones."""
    block = _get_memory_context(
        None,
        app_config=FakeConfig(),
        user_id="user-2--project--y",
        shared_user_ids=(SHARED_BUCKET,),
    )

    assert "approved shared fact" in block
    assert block.strip().startswith("<memory>")


def test_no_memory_anywhere_produces_no_block(manager: FakeManager) -> None:
    assert _get_memory_context(None, app_config=FakeConfig(), user_id="empty", shared_user_ids=("also-empty",)) == ""


def test_a_failing_shared_bucket_does_not_lose_private_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """One unreadable bucket must not blank the whole injection."""

    class Flaky(FakeManager):
        def get_context(self, *, user_id: str, agent_name: str | None = None) -> str:
            if user_id == SHARED_BUCKET:
                raise RuntimeError("shared bucket unavailable")
            return super().get_context(user_id=user_id, agent_name=agent_name)

    monkeypatch.setattr(
        "deerflow.agents.memory.get_memory_manager",
        lambda: Flaky({"user-1--project--x": "- private fact"}),
    )

    block = _get_memory_context(
        None,
        app_config=FakeConfig(),
        user_id="user-1--project--x",
        shared_user_ids=(SHARED_BUCKET,),
    )

    assert "private fact" in block
    assert "project_shared_memory" not in block


def test_injection_disabled_short_circuits_every_bucket(manager: FakeManager) -> None:
    class Disabled(FakeConfig):
        class memory:  # noqa: N801
            enabled = True
            injection_enabled = False

    assert _get_memory_context(None, app_config=Disabled(), user_id="user-1--project--x", shared_user_ids=(SHARED_BUCKET,)) == ""
    assert manager.calls == []
