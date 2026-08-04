"""A DBTL stage worker is visible while it works.

Until now `LiveStageAdapter` awaited one terminal result per work unit, so a
Build worker was one opaque stretch: no reads, no tools, no Bash, no output —
and when its structured result was rejected, the only visible fact was that the
stage had failed. The `task` tool has streamed its subagent's steps since #3779;
this routes DBTL workers through the same helper so both paths report progress
the same way and the frontend needs no second renderer.

What matters here is not that events are emitted eventually, but that the
**worker's own step list is what produces them**, unit by unit, without the
adapter growing a second copy of the cursor.
"""

import asyncio
import sys
import types
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from deerflow.agents.dbtl.live_stage import adapter as adapter_module
from deerflow.dbtl.stage_runner import WorkerBudget, WorkUnit

pytestmark = pytest.mark.asyncio


class _FakeResult:
    """Stands in for `SubagentResult`, which conftest replaces with a mock."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.trace_id = "trace"
        self.status = _FakeStatus.PENDING
        self.result: str | None = None
        self.error: str | None = None
        self.stop_reason = None
        self.ai_messages: list[dict] = []
        self.token_usage_records: list[dict] = []
        self.cancel_event = asyncio.Event()


class _FakeStatus:
    PENDING = "pending"
    COMPLETED = "completed"


@dataclass
class _FakeConfig:
    """A real dataclass: the adapter pins the effective model with `replace`."""

    name: str
    model: str = "inherit"
    max_turns: int = 40
    timeout_seconds: int = 600
    skills: list[str] | None = None


class _FakeExecutor:
    """Appends steps into the holder the way the real executor does."""

    steps: list[dict] = []
    answer = '{"status": "completed", "summary": "done"}'
    last_kwargs: dict = {}

    def __init__(self, **kwargs) -> None:
        self.trace_id = "trace"
        self.kwargs = kwargs
        type(self).last_kwargs = kwargs

    def execute(self, prompt: str, holder):
        for step in self.steps:
            holder.ai_messages.append(step)
        holder.status = _FakeStatus.COMPLETED
        holder.result = self.answer
        return holder


@pytest.fixture
def dispatch(monkeypatch):
    """Drive `_dispatch_units` with a fake executor and capture stream events."""
    from deerflow.agents.dbtl.live_stage import adapter as adapter_module

    events: list[dict] = []
    _FakeExecutor.answer = '{"status": "completed", "summary": "done"}'

    monkeypatch.setattr("langgraph.config.get_stream_writer", lambda: events.append)

    subagents = types.ModuleType("deerflow.subagents")
    subagents.SubagentExecutor = _FakeExecutor
    subagents.get_subagent_config = lambda name, app_config=None: _FakeConfig(name=name)
    monkeypatch.setitem(sys.modules, "deerflow.subagents", subagents)

    executor_module = types.ModuleType("deerflow.subagents.executor")
    executor_module.SubagentResult = lambda task_id, trace_id, status: _FakeResult(task_id)
    executor_module.SubagentStatus = _FakeStatus
    monkeypatch.setitem(sys.modules, "deerflow.subagents.executor", executor_module)

    monkeypatch.setattr(adapter_module, "_stage_worker_config", lambda config, budget: config)
    monkeypatch.setattr(adapter_module, "_tools_for_stage_budget", lambda tools, budget: tools)
    # Accept whatever keywords the real budget helper grows. This stub exists to
    # pin the *step stream*, not the deadline arithmetic, so a new keyword there
    # should not fail eight progress tests with a TypeError that says nothing
    # about progress.
    monkeypatch.setattr(adapter_module, "_model_call_budget", lambda turns, **_: 6)
    monkeypatch.setattr(adapter_module, "_report_subagent_token_usage", lambda config, result: None)
    monkeypatch.setattr(adapter_module, "_summarize_token_usage", lambda records: None)

    tools_module = types.ModuleType("deerflow.tools")
    tools_module.get_available_tools = lambda **kwargs: []
    monkeypatch.setitem(sys.modules, "deerflow.tools", tools_module)

    async def run(units, *, stage="build", meeting=False, budget=None):
        adapter = adapter_module.LiveStageAdapter(repo=object(), app_config=None)
        outcomes = await adapter._dispatch_units(
            units,
            budget=budget or WorkerBudget(),
            config={},
            state={},
            runtime={"run_id": "run-1", "thread_id": "thread-1"},
            metadata={},
            project_id="proj-1",
            project_root="/tmp/project",
            cycle_id="cycle-1",
            stage=stage,
            meeting=meeting,
            stage_workspace=None,
        )
        return outcomes, events

    return run


def _unit(unit_id: str = "unit-1") -> WorkUnit:
    return WorkUnit(unit_id=unit_id, capability="software_and_workflow_engineering", agent_name="general-purpose", prompt="Do the build.")


class TestAStageWorkerReportsItsSteps:
    async def test_build_preflight_refuses_before_dispatch_when_bash_is_absent(self, monkeypatch):
        tools_module = types.ModuleType("deerflow.tools")
        tools_module.get_available_tools = lambda **kwargs: [SimpleNamespace(name="read_file")]
        monkeypatch.setitem(sys.modules, "deerflow.tools", tools_module)
        adapter = adapter_module.LiveStageAdapter(repo=object(), app_config=None)

        refusal = adapter._build_execution_preflight_error(
            config={},
            stage_workspace="/mnt/user-data/outputs/.dbtl-stage-work/run/build",
        )

        assert "no Bash execution tool" in refusal
        assert "No planner or Build worker ran" in refusal

    async def test_build_preflight_accepts_the_effective_bash_tool(self, monkeypatch):
        tools_module = types.ModuleType("deerflow.tools")
        tools_module.get_available_tools = lambda **kwargs: [SimpleNamespace(name="bash")]
        monkeypatch.setitem(sys.modules, "deerflow.tools", tools_module)
        adapter = adapter_module.LiveStageAdapter(repo=object(), app_config=None)

        assert (
            adapter._build_execution_preflight_error(
                config={},
                stage_workspace="/mnt/user-data/outputs/.dbtl-stage-work/run/build",
            )
            == ""
        )

    async def test_legacy_uncapped_build_gets_the_operational_safety_ceiling(self, dispatch):
        budget = WorkerBudget(
            max_workers=3,
            max_turns=10_000,
            max_tokens=1_000_000,
            timeout_seconds=900,
            token_limit_enforced=False,
        )

        await dispatch([_unit()], budget=budget)

        assert _FakeExecutor.last_kwargs["config"].max_turns == 450
        assert _FakeExecutor.last_kwargs["token_budget_max_tokens"] == 120_000
        assert _FakeExecutor.last_kwargs["token_budget_enabled"] is None
        assert _FakeExecutor.last_kwargs["loop_detection_enabled"] is None
        assert len(_FakeExecutor.last_kwargs["extra_middlewares"]) == 1

    async def test_each_captured_step_becomes_a_running_event(self, dispatch):
        _FakeExecutor.steps = [
            {"type": "ai", "content": "Reading the inputs", "tool_calls": [{"name": "read_file", "args": {"path": "/mnt/user-data/x.csv"}}]},
            {"type": "tool", "name": "read_file", "content": "col_a,col_b"},
        ]

        _, events = await dispatch([_unit()])

        running = [event for event in events if event["type"] == "task_running"]
        assert [event["message"]["type"] for event in running] == ["ai", "tool"]
        assert [event["message_index"] for event in running] == [1, 2]

    async def test_running_events_name_the_stage_and_unit(self, dispatch):
        _FakeExecutor.steps = [{"type": "tool", "name": "bash", "content": "ok"}]

        _, events = await dispatch([_unit("build-worker-7")], stage="build")

        running = next(event for event in events if event["type"] == "task_running")
        assert running["task_id"] == "build-worker-7"
        assert running["dbtl_stage"] == "build"

    async def test_ordinary_stage_work_is_not_labelled_as_a_meeting(self, dispatch):
        # The frontend keys its meeting copy on `council_seat`; Build work
        # carrying one would render as a design meeting.
        _FakeExecutor.steps = [{"type": "tool", "name": "bash", "content": "ok"}]

        _, events = await dispatch([_unit()], meeting=False)

        assert all("council_seat" not in event for event in events)

    async def test_a_meeting_seat_does_not_repeat_its_identity_per_step(self, dispatch):
        # The seat is asserted once, at `task_started`.
        _FakeExecutor.steps = [{"type": "tool", "name": "read_file", "content": "ok"}]

        _, events = await dispatch([_unit()], stage="design", meeting=True)

        assert any("council_seat" in event for event in events if event["type"] == "task_started")
        assert all("council_seat" not in event for event in events if event["type"] == "task_running")

    async def test_each_unit_keeps_its_own_cursor(self, dispatch):
        _FakeExecutor.steps = [{"type": "tool", "name": "bash", "content": "ok"}]

        _, events = await dispatch([_unit("a"), _unit("b")])

        running = [event for event in events if event["type"] == "task_running"]
        assert sorted(event["task_id"] for event in running) == ["a", "b"]
        # Both start at 1: a shared cursor would number the second unit's only
        # step as its second.
        assert [event["message_index"] for event in running] == [1, 1]

    async def test_a_worker_with_no_steps_reports_only_its_lifecycle(self, dispatch):
        _FakeExecutor.steps = []

        _, events = await dispatch([_unit()])

        # The worker's activity row rides the same writer; this is about the
        # task lifecycle not gaining a step it never took.
        assert [event["type"] for event in events if event["type"].startswith("task_")] == ["task_started", "task_completed"]

    async def test_contract_rejection_fails_the_worker_activity_too(self, dispatch):
        _FakeExecutor.steps = []
        _FakeExecutor.answer = "I finished, but did not return the contract."

        _, events = await dispatch([_unit()])

        assert next(event for event in events if event["type"] == "task_failed")
        terminal_activity = next(event for event in reversed(events) if event.get("type") == "agent_activity" and event.get("actor_kind") == "stage_worker" and event.get("transition") in {"completed", "failed"})
        assert terminal_activity["transition"] == "failed"
        assert terminal_activity["state"] == "failed"
