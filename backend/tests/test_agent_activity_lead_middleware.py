"""The lead agent reports its own presence.

Until this existed, only DBTL project runs emitted activity: an ordinary chat, a
projectless conversation, a custom agent, an IM turn, and a scheduled run all
produced nothing at all, so the projection described the one code path that was
already the most legible and stayed silent for every other.

An agent cannot use ``activity_span`` — it begins in one graph node and ends in
another — so the middleware holds the handle across hooks and owes a close on
every exit. These tests are mostly about that debt.
"""

from types import SimpleNamespace

import pytest

from deerflow.agents.middlewares.agent_activity_middleware import AgentActivityMiddleware
from deerflow.runtime.activity.vocabulary import ActivityState, ActorKind

pytestmark = pytest.mark.asyncio


class _Writer:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    def __call__(self, payload: dict) -> None:
        self.frames.append(payload)


def _runtime(run_id: str | None = "run-1") -> SimpleNamespace:
    return SimpleNamespace(context={"run_id": run_id} if run_id else {})


@pytest.fixture
def writer(monkeypatch) -> _Writer:
    """Stand in for the stream writer LangGraph hands to the current node.

    The handle resolves it per emit rather than capturing one, because the
    middleware's hooks run in different graph nodes and a captured writer would
    belong to a task that has already finished — so the patch has to cover the
    whole test, not just the opening hook.
    """
    recorder = _Writer()
    monkeypatch.setattr("deerflow.runtime.activity.emitter.resolve_writer", lambda: recorder)
    return recorder


def _middleware() -> AgentActivityMiddleware:
    return AgentActivityMiddleware()


class TestAnOrdinaryRunIsVisible:
    async def test_the_agent_opens_and_closes_one_row(self, writer):
        middleware = _middleware()

        await middleware.abefore_agent({}, _runtime())
        await middleware.aafter_agent({}, _runtime())

        assert [frame["transition"] for frame in writer.frames] == ["started", "completed"]
        assert {frame["actor_kind"] for frame in writer.frames} == {ActorKind.LEAD_AGENT.value}
        assert {frame["display_name"] for frame in writer.frames} == {"Lead agent"}

    async def test_a_model_call_reports_thinking(self, writer):
        middleware = _middleware()
        await middleware.abefore_agent({}, _runtime())

        async def handler(request):
            return "response"

        await middleware.awrap_model_call(SimpleNamespace(runtime=_runtime()), handler)
        await middleware.aafter_agent({}, _runtime())

        assert [frame["state"] for frame in writer.frames] == ["preparing", "thinking", "completed"]

    async def test_delegating_reads_as_waiting_rather_than_working(self, writer):
        middleware = _middleware()
        await middleware.abefore_agent({}, _runtime())

        async def handler(request):
            return "result"

        await middleware.awrap_tool_call(
            SimpleNamespace(runtime=_runtime(), tool_call={"name": "task"}),
            handler,
        )

        assert writer.frames[-1]["state"] == ActivityState.WAITING.value
        assert writer.frames[-1]["operation"] == "Waiting for a subtask"

    async def test_an_ordinary_tool_reads_as_working(self, writer):
        middleware = _middleware()
        await middleware.abefore_agent({}, _runtime())

        async def handler(request):
            return "result"

        await middleware.awrap_tool_call(
            SimpleNamespace(runtime=_runtime(), tool_call={"name": "bash"}),
            handler,
        )

        assert writer.frames[-1]["state"] == ActivityState.COMPUTING.value

    async def test_nested_ordinary_branch_recovers_the_outer_run_id(self, writer, monkeypatch):
        middleware = _middleware()
        monkeypatch.setattr(
            "deerflow.agents.middlewares.agent_activity_middleware.get_config",
            lambda: {
                "configurable": {
                    "context": {"run_id": "run-supervised-ordinary"},
                }
            },
        )
        runtime = SimpleNamespace(context={})

        await middleware.abefore_agent({}, runtime)
        await middleware.aafter_agent({}, runtime)

        assert [frame["transition"] for frame in writer.frames] == ["started", "completed"]
        assert {frame["run_id"] for frame in writer.frames} == {"run-supervised-ordinary"}
        assert {frame["display_name"] for frame in writer.frames} == {"Lead agent"}

    async def test_no_tool_name_reaches_the_projection(self, writer):
        middleware = _middleware()
        await middleware.abefore_agent({}, _runtime())

        async def handler(request):
            return "result"

        await middleware.awrap_tool_call(
            SimpleNamespace(runtime=_runtime(), tool_call={"name": "read_file", "args": {"path": "/home/user/.ssh/id_rsa"}}),
            handler,
        )

        rendered = " ".join(str(value) for frame in writer.frames for value in frame.values())
        assert "read_file" not in rendered
        assert "id_rsa" not in rendered


class TestTheHookStillHandsBackWhatItWrapped:
    """Instrumentation that changes a result is worse than none."""

    async def test_the_model_response_is_returned_unchanged(self):
        middleware = _middleware()
        await middleware.abefore_agent({}, _runtime())
        sentinel = object()

        async def handler(request):
            return sentinel

        assert await middleware.awrap_model_call(SimpleNamespace(runtime=_runtime()), handler) is sentinel

    async def test_a_raising_tool_still_raises(self):
        middleware = _middleware()
        await middleware.abefore_agent({}, _runtime())

        async def handler(request):
            raise RuntimeError("the tool failed")

        with pytest.raises(RuntimeError, match="the tool failed"):
            await middleware.awrap_tool_call(SimpleNamespace(runtime=_runtime(), tool_call={"name": "bash"}), handler)


class TestARunWithoutIdentityEmitsNothing:
    """A row keyed to no run cannot be reconciled with anything."""

    async def test_a_missing_run_id_opens_no_row(self, writer):
        middleware = _middleware()

        await middleware.abefore_agent({}, _runtime(run_id=None))
        await middleware.aafter_agent({}, _runtime(run_id=None))

        assert writer.frames == []

    async def test_a_model_call_without_an_open_row_emits_nothing(self, writer):
        middleware = _middleware()

        async def handler(request):
            return "response"

        await middleware.awrap_model_call(SimpleNamespace(runtime=_runtime()), handler)
        assert writer.frames == []


class TestConcurrentRunsDoNotCloseEachOther:
    """The middleware is built once per agent and the agent is cached."""

    async def test_two_runs_keep_separate_rows(self, writer):
        middleware = _middleware()

        await middleware.abefore_agent({}, _runtime("run-a"))
        await middleware.abefore_agent({}, _runtime("run-b"))
        await middleware.aafter_agent({}, _runtime("run-a"))

        closed = [frame for frame in writer.frames if frame["transition"] == "completed"]
        assert [frame["run_id"] for frame in closed] == ["run-a"]

        await middleware.aafter_agent({}, _runtime("run-b"))
        closed = [frame for frame in writer.frames if frame["transition"] == "completed"]
        assert [frame["run_id"] for frame in closed] == ["run-a", "run-b"]

    async def test_a_second_before_hook_for_one_run_does_not_reopen_its_row(self, writer):
        middleware = _middleware()

        await middleware.abefore_agent({}, _runtime())
        await middleware.abefore_agent({}, _runtime())

        assert [frame["transition"] for frame in writer.frames] == ["started"]

    async def test_tracked_runs_are_bounded(self, writer):
        middleware = _middleware()

        for index in range(80):
            await middleware.abefore_agent({}, _runtime(f"run-{index}"))

        assert len(middleware._handles) <= 64
        # Evicted rows are settled rather than silently forgotten: a row nothing
        # will ever close is the failure this projection exists to remove.
        assert any(frame["transition"] == "interrupted" for frame in writer.frames)


class TestTheMiddlewareIsInTheLeadChain:
    async def test_the_chain_carries_exactly_one_activity_middleware(self):
        from deerflow.agents.lead_agent.agent import build_middlewares
        from deerflow.config.app_config import AppConfig
        from deerflow.config.sandbox_config import SandboxConfig

        middlewares = build_middlewares(
            {"configurable": {}},
            model_name=None,
            app_config=AppConfig(sandbox=SandboxConfig(use="test")),
        )
        assert sum(isinstance(item, AgentActivityMiddleware) for item in middlewares) == 1
