"""Project conversations use a project-specific memory bucket."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.memory.scope import scoped_memory_user_id
from deerflow.agents.middlewares.memory_middleware import MemoryMiddleware
from deerflow.config.memory_config import MemoryConfig
from deerflow.runtime.context_keys import CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY


def test_project_scoped_run_is_queued_into_its_own_memory_bucket(monkeypatch):
    manager = MagicMock()
    monkeypatch.setattr(
        "deerflow.agents.middlewares.memory_middleware.get_memory_manager",
        lambda: manager,
    )
    monkeypatch.setattr(
        "deerflow.agents.middlewares.memory_middleware.resolve_runtime_user_id",
        lambda runtime: "user-1",
    )
    middleware = MemoryMiddleware(memory_config=MemoryConfig(enabled=True))
    state = {
        "messages": [
            HumanMessage(content="Old unrelated project context", id="old-user"),
            AIMessage(content="Old unrelated answer", id="old-ai"),
            HumanMessage(content="We are working in test2", id="current-user"),
            AIMessage(content="The current project is test2.", id="current-ai"),
        ]
    }
    runtime = SimpleNamespace(
        context={
            "thread_id": "thread-test2",
            "project_id": "project-test2",
            "project_root": "/Users/jyang21/Documents/projects/test2",
            CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY: frozenset({"old-user", "old-ai"}),
        }
    )

    assert middleware.after_agent(state, runtime) is None
    manager.add.assert_called_once()
    args, kwargs = manager.add.call_args
    assert args[0] == "thread-test2"
    assert [message.id for message in args[1]] == ["current-user", "current-ai"]
    assert kwargs["user_id"] == scoped_memory_user_id(
        "user-1",
        {
            "project_id": "project-test2",
            "project_root": "/Users/jyang21/Documents/projects/test2",
        },
    )


def test_unfiled_run_keeps_the_existing_user_global_memory_bucket(monkeypatch):
    manager = MagicMock()
    monkeypatch.setattr(
        "deerflow.agents.middlewares.memory_middleware.get_memory_manager",
        lambda: manager,
    )
    monkeypatch.setattr(
        "deerflow.agents.middlewares.memory_middleware.resolve_runtime_user_id",
        lambda runtime: "user-1",
    )
    middleware = MemoryMiddleware(memory_config=MemoryConfig(enabled=True))
    state = {
        "messages": [
            HumanMessage(content="Remember that I prefer concise answers"),
            AIMessage(content="I will remember that."),
        ]
    }

    assert (
        middleware.after_agent(
            state,
            SimpleNamespace(context={"thread_id": "thread-unfiled"}),
        )
        is None
    )

    assert manager.add.call_args.kwargs["user_id"] == "user-1"
