"""Tests for ensure_sandbox_initialized with fork-restored channel values."""

from __future__ import annotations

import pytest
from langchain.tools import ToolRuntime
from langgraph.types import Overwrite

from deerflow.sandbox.sandbox import Sandbox
from deerflow.sandbox.sandbox_provider import SandboxProvider, reset_sandbox_provider, set_sandbox_provider
from deerflow.sandbox.search import GrepMatch
from deerflow.sandbox.tools import ensure_sandbox_initialized, ensure_sandbox_initialized_async


class _StubSandbox(Sandbox):
    def execute_command(self, command: str, env: dict | None = None, timeout: float | None = None) -> str:
        del env, timeout
        return "OK"

    def read_file(self, path: str) -> str:
        return "content"

    def download_file(self, path: str) -> bytes:
        return b"content"

    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        return ["/mnt/user-data/workspace/file.txt"]

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        return None

    def glob(self, path: str, pattern: str, *, include_dirs: bool = False, max_results: int = 200) -> tuple[list[str], bool]:
        return [], False

    def grep(
        self,
        path: str,
        pattern: str,
        *,
        glob: str | None = None,
        literal: bool = False,
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> tuple[list[GrepMatch], bool]:
        return [], False

    def update_file(self, path: str, content: bytes) -> None:
        return None


class _RecordingProvider(SandboxProvider):
    def __init__(self) -> None:
        self.sandbox = _StubSandbox("stub")

    def acquire(
        self,
        thread_id: str | None = None,
        *,
        user_id: str | None = None,
        project_id: str | None = None,
        project_root: str | None = None,
    ) -> str:
        raise AssertionError("state already carries a sandbox; acquire must not run")

    async def acquire_async(
        self,
        thread_id: str | None = None,
        *,
        user_id: str | None = None,
        project_id: str | None = None,
        project_root: str | None = None,
    ) -> str:
        raise AssertionError("state already carries a sandbox; acquire must not run")

    def get(self, sandbox_id: str) -> Sandbox | None:
        if sandbox_id == "parent-sandbox":
            return self.sandbox
        return None

    def release(self, sandbox_id: str) -> None:
        return None


class _FallthroughProvider(SandboxProvider):
    """Provider whose parent id has expired, forcing a fresh acquire."""

    def __init__(self) -> None:
        self.sandbox = _StubSandbox("fresh")
        self.acquired: list[tuple[str | None, str | None, str | None]] = []

    def acquire(
        self,
        thread_id: str | None = None,
        *,
        user_id: str | None = None,
        project_id: str | None = None,
        project_root: str | None = None,
    ) -> str:
        self.acquired.append((thread_id, project_id, project_root))
        return "fresh-sandbox"

    async def acquire_async(
        self,
        thread_id: str | None = None,
        *,
        user_id: str | None = None,
        project_id: str | None = None,
        project_root: str | None = None,
    ) -> str:
        self.acquired.append((thread_id, project_id, project_root))
        return "fresh-sandbox"

    def get(self, sandbox_id: str) -> Sandbox | None:
        if sandbox_id == "fresh-sandbox":
            return self.sandbox
        return None

    def release(self, sandbox_id: str) -> None:
        return None


class _ProjectScopeChangeProvider(_FallthroughProvider):
    """A cached conversation sandbox that predates project membership."""

    def __init__(self) -> None:
        super().__init__()
        self.old_sandbox = _StubSandbox("old")

    def get(self, sandbox_id: str) -> Sandbox | None:
        if sandbox_id == "conversation-sandbox":
            return self.old_sandbox
        return super().get(sandbox_id)


def _make_runtime(state: dict) -> ToolRuntime:
    return ToolRuntime(
        state=state,
        context={},
        config={"configurable": {}},
        stream_writer=lambda _: None,
        tools=[],
        tool_call_id="call-1",
        store=None,
    )


def test_ensure_sandbox_initialized_unwraps_overwrite_state() -> None:
    """Fork-restored state must not crash on the Overwrite wrapper."""
    provider = _RecordingProvider()
    set_sandbox_provider(provider)
    try:
        runtime = _make_runtime({"sandbox": Overwrite({"sandbox_id": "parent-sandbox"})})
        sandbox = ensure_sandbox_initialized(runtime)
    finally:
        reset_sandbox_provider()

    assert sandbox is provider.sandbox
    assert runtime.context["sandbox_id"] == "parent-sandbox"
    # The reuse path must not take ownership: the wrapped state is left
    # untouched, so after_agent still sees fork_restored and skips release.
    assert isinstance(runtime.state["sandbox"], Overwrite)


@pytest.mark.anyio
async def test_ensure_sandbox_initialized_async_unwraps_overwrite_state() -> None:
    provider = _RecordingProvider()
    set_sandbox_provider(provider)
    try:
        runtime = _make_runtime({"sandbox": Overwrite({"sandbox_id": "parent-sandbox"})})
        sandbox = await ensure_sandbox_initialized_async(runtime)
    finally:
        reset_sandbox_provider()

    assert sandbox is provider.sandbox
    assert runtime.context["sandbox_id"] == "parent-sandbox"


def test_ensure_sandbox_initialized_plain_state_unchanged() -> None:
    provider = _RecordingProvider()
    set_sandbox_provider(provider)
    try:
        runtime = _make_runtime({"sandbox": {"sandbox_id": "parent-sandbox"}})
        sandbox = ensure_sandbox_initialized(runtime)
    finally:
        reset_sandbox_provider()

    assert sandbox is provider.sandbox
    assert runtime.context["sandbox_id"] == "parent-sandbox"


def test_ensure_sandbox_initialized_acquires_fresh_when_parent_missing() -> None:
    """Acquire fall-through: the fork-restored id is gone from the provider,
    so a fresh sandbox is acquired and the stale wrapped state is replaced
    by the freshly acquired plain dict."""
    provider = _FallthroughProvider()
    set_sandbox_provider(provider)
    try:
        runtime = _make_runtime({"sandbox": Overwrite({"sandbox_id": "parent-sandbox"})})
        runtime.context["thread_id"] = "t-1"
        sandbox = ensure_sandbox_initialized(runtime)
    finally:
        reset_sandbox_provider()

    assert provider.acquired == [("t-1", None, None)]
    assert sandbox is provider.sandbox
    assert runtime.state["sandbox"] == {"sandbox_id": "fresh-sandbox"}
    assert runtime.context["sandbox_id"] == "fresh-sandbox"


@pytest.mark.anyio
async def test_ensure_sandbox_initialized_async_plain_state_unchanged() -> None:
    provider = _RecordingProvider()
    set_sandbox_provider(provider)
    try:
        runtime = _make_runtime({"sandbox": {"sandbox_id": "parent-sandbox"}})
        sandbox = await ensure_sandbox_initialized_async(runtime)
    finally:
        reset_sandbox_provider()

    assert sandbox is provider.sandbox
    assert runtime.context["sandbox_id"] == "parent-sandbox"


@pytest.mark.anyio
async def test_ensure_sandbox_initialized_async_acquires_fresh_when_parent_missing() -> None:
    """Same fall-through as the sync path: the fork-restored id is gone from
    the provider, so a fresh sandbox is acquired and the stale wrapped state
    is replaced by the freshly acquired plain dict."""
    provider = _FallthroughProvider()
    set_sandbox_provider(provider)
    try:
        runtime = _make_runtime({"sandbox": Overwrite({"sandbox_id": "parent-sandbox"})})
        runtime.context["thread_id"] = "t-1"
        sandbox = await ensure_sandbox_initialized_async(runtime)
    finally:
        reset_sandbox_provider()

    assert provider.acquired == [("t-1", None, None)]
    assert sandbox is provider.sandbox
    assert runtime.state["sandbox"] == {"sandbox_id": "fresh-sandbox"}
    assert runtime.context["sandbox_id"] == "fresh-sandbox"


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.anyio
async def test_ensure_sandbox_initialized_preserves_project_scope_on_lazy_acquire(async_mode: bool) -> None:
    provider = _FallthroughProvider()
    set_sandbox_provider(provider)
    try:
        runtime = _make_runtime({})
        runtime.context.update(
            {
                "thread_id": "t-project",
                "project_id": "project-1",
                "project_root": "/projects/one",
            }
        )
        if async_mode:
            sandbox = await ensure_sandbox_initialized_async(runtime)
        else:
            sandbox = ensure_sandbox_initialized(runtime)
    finally:
        reset_sandbox_provider()

    assert sandbox is provider.sandbox
    assert provider.acquired == [("t-project", "project-1", "/projects/one")]
    assert runtime.state["thread_data"] == {
        "workspace_path": "/projects/one",
        "uploads_path": "/projects/one/uploads",
        "outputs_path": "/projects/one/outputs",
    }


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.anyio
async def test_cached_conversation_sandbox_is_rebuilt_after_project_filing(async_mode: bool) -> None:
    provider = _ProjectScopeChangeProvider()
    set_sandbox_provider(provider)
    try:
        runtime = _make_runtime({"sandbox": {"sandbox_id": "conversation-sandbox"}})
        runtime.context.update(
            {
                "thread_id": "t-filed",
                "project_id": "project-1",
                "project_root": "/projects/one",
            }
        )
        if async_mode:
            sandbox = await ensure_sandbox_initialized_async(runtime)
        else:
            sandbox = ensure_sandbox_initialized(runtime)
    finally:
        reset_sandbox_provider()

    assert sandbox is provider.sandbox
    assert provider.acquired == [("t-filed", "project-1", "/projects/one")]
    assert runtime.state["sandbox"] == {
        "sandbox_id": "fresh-sandbox",
        "project_id": "project-1",
        "project_root": "/projects/one",
    }
