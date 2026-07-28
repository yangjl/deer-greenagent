"""A stage worker must be able to reach its own result contract.

The DBTL Design council failed in a way that looked like three bad workers and
was actually one missing mechanism. Every seat ran until ``recursion_limit``
raised ``GraphRecursionError``; the executor then recovered "the last AIMessage
with text", which mid-tool-loop is prose or nothing. The structured contract
those workers are graded against can only be satisfied by a *final* message, so
a worker that spends its budget can never produce evidence no matter how good
its investigation was.

Token and loop guards already solve this shape: they strip ``tool_calls`` so the
loop ends naturally and the model writes a final answer. The turn axis had no
such guard, which is the asymmetry these tests pin.
"""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.finalization_deadline_middleware import (
    DEFAULT_RESERVE_CALLS,
    FinalizationDeadlineMiddleware,
)


def _runtime(run_id: str = "run-1"):
    runtime = MagicMock()
    runtime.context = {"thread_id": "thread-1", "run_id": run_id}
    return runtime


def _ai(content: str = "", *, tool_calls=None, msg_id: str | None = None) -> AIMessage:
    return AIMessage(
        id=msg_id,
        content=content,
        tool_calls=tool_calls or [],
        response_metadata={"finish_reason": "tool_calls"} if tool_calls else {},
    )


def _tool_call(index: int) -> dict:
    return {"name": "read_file", "args": {"path": f"/tmp/{index}"}, "id": f"call-{index}"}


def _state(turns: int) -> dict:
    """History as ``after_model`` sees it: ending on the model's own message.

    Each completed turn is an AIMessage requesting a tool plus that tool's
    result; the newest turn has no result yet, because the middleware runs
    between the model node and the tool node.
    """
    messages: list = [HumanMessage(content="design the study")]
    for index in range(turns):
        messages.append(_ai(tool_calls=[_tool_call(index)], msg_id=f"ai-{index}"))
        if index < turns - 1:
            messages.append(ToolMessage(content="ok", tool_call_id=f"call-{index}"))
    return {"messages": messages}


def _make_request(messages, runtime):
    request = MagicMock()
    request.messages = list(messages)
    request.runtime = runtime

    def override_fn(messages=None, **_kwargs):
        replacement = MagicMock()
        replacement.messages = messages if messages is not None else request.messages
        replacement.runtime = request.runtime
        return replacement

    request.override = override_fn
    return request


class TestDeadlineWarning:
    def test_stays_silent_while_the_worker_has_room(self):
        middleware = FinalizationDeadlineMiddleware(max_model_calls=10, reserve_calls=3)
        runtime = _runtime()

        assert middleware._apply(_state(2), runtime) is None
        assert middleware.drain_pending_notices(runtime) == []

    def test_warns_once_when_the_reserve_opens(self):
        middleware = FinalizationDeadlineMiddleware(max_model_calls=10, reserve_calls=3)
        runtime = _runtime()

        middleware._apply(_state(7), runtime)
        notices = middleware.drain_pending_notices(runtime)

        assert len(notices) == 1
        assert "3 model call" in notices[0]
        # The instruction has to name the deliverable, not just the shortage:
        # "you are running out of turns" tells a worker to hurry, not to emit
        # the one message shape its result is parsed from.
        assert "final message" in notices[0].lower()

        middleware._apply(_state(8), runtime)
        assert middleware.drain_pending_notices(runtime) == []

    def test_notice_is_injected_into_the_next_model_request(self):
        middleware = FinalizationDeadlineMiddleware(max_model_calls=10, reserve_calls=3)
        runtime = _runtime()
        middleware._apply(_state(7), runtime)

        request = _make_request([HumanMessage(content="design the study")], runtime)
        captured: list = []

        def handler(req):
            captured.append(req)
            return MagicMock()

        middleware.wrap_model_call(request, handler)

        assert len(captured) == 1
        injected = captured[0].messages[-1]
        assert isinstance(injected, HumanMessage)
        assert "final message" in injected.content.lower()


class TestForcedFinalization:
    def test_strips_tool_calls_on_the_last_available_call(self):
        middleware = FinalizationDeadlineMiddleware(max_model_calls=10, reserve_calls=3)
        runtime = _runtime()

        update = middleware._apply(_state(10), runtime)

        assert update is not None
        finalized = update["messages"][0]
        assert finalized.tool_calls == []
        assert finalized.response_metadata.get("finish_reason") == "stop"

    def test_forced_finalization_is_reported_as_a_limitation_not_a_cap(self):
        """A met deadline is not a truncated investigation.

        ``was_capped`` exists so a run that hit a guardrail cannot be filed as
        finished work. A worker that was told "finalize now" and did so is a
        different thing: it answered on time. Reporting it through
        ``consume_stop_reason`` would fold it into ``CAPPED_STOP_REASONS`` and
        discard the evidence for the same reason the cap does — so it is
        surfaced as an inspectable flag instead, and the caller decides how to
        render it for a reviewer.
        """
        middleware = FinalizationDeadlineMiddleware(max_model_calls=10, reserve_calls=3)
        runtime = _runtime()

        middleware._apply(_state(10), runtime)

        assert middleware.forced_finalization(runtime) is True
        assert not hasattr(middleware, "consume_stop_reason")

    def test_a_worker_that_finished_early_is_never_flagged(self):
        middleware = FinalizationDeadlineMiddleware(max_model_calls=10, reserve_calls=3)
        runtime = _runtime()

        state = {"messages": [HumanMessage(content="design"), _ai("here is my JSON", msg_id="ai-final")]}
        assert middleware._apply(state, runtime) is None
        assert middleware.forced_finalization(runtime) is False

    def test_a_final_text_answer_at_the_deadline_is_left_alone(self):
        """Nothing to force when the model already stopped calling tools."""
        middleware = FinalizationDeadlineMiddleware(max_model_calls=10, reserve_calls=3)
        runtime = _runtime()

        messages = _state(9)["messages"]
        messages.append(_ai('{"status": "completed"}', msg_id="ai-final"))

        assert middleware._apply({"messages": messages}, runtime) is None
        assert middleware.forced_finalization(runtime) is False


class TestRunIsolation:
    def test_two_runs_do_not_share_a_deadline(self):
        middleware = FinalizationDeadlineMiddleware(max_model_calls=10, reserve_calls=3)

        middleware._apply(_state(10), _runtime("run-a"))

        assert middleware.forced_finalization(_runtime("run-a")) is True
        assert middleware.forced_finalization(_runtime("run-b")) is False


class TestStageWiring:
    """The deadline only helps if the stage layer derives and reports it."""

    def test_model_call_budget_halves_the_recursion_limit(self):
        from deerflow.agents.dbtl.stage_execution import _model_call_budget

        # ``max_turns`` is LangGraph's recursion_limit, which counts super-steps;
        # a tool-calling turn costs two. A deadline computed from the raw number
        # would fire after the run had already been aborted.
        assert _model_call_budget(40) == 20
        assert _model_call_budget(80) == 40

    def test_model_call_budget_never_leaves_less_room_than_the_reserve(self):
        from deerflow.agents.dbtl.stage_execution import _model_call_budget

        assert _model_call_budget(2) >= DEFAULT_RESERVE_CALLS + 1
        assert _model_call_budget(0) >= DEFAULT_RESERVE_CALLS + 1

    def test_a_forced_worker_reports_a_limitation_and_stays_trustworthy(self):
        """The whole point: deadline work is evidence, capped work is not."""
        import json

        from deerflow.dbtl.agent_selector import SelectionResult
        from deerflow.dbtl.stage_runner import DispatchOutcome, StageExecutionPlan, WorkUnit, collect_results
        from deerflow.dbtl.stage_spec import resolve_stage_spec

        spec = resolve_stage_spec("design")
        unit = WorkUnit(unit_id="u-1", capability="experimental_design", agent_name="general-purpose", prompt="p")
        plan = StageExecutionPlan(spec=spec, selection=SelectionResult(), units=(unit,))
        payload = json.dumps(
            {
                "status": "completed",
                "summary": "A design that names its controls.",
                "claims": ["Randomized complete block is adequate here."],
                "evidence_refs": [{"kind": "external", "reference": "position-1", "description": "council position"}],
                "quality_checks": [{"name": "controls named", "passed": True, "detail": ""}],
            }
        )

        outcome = collect_results(
            plan,
            [DispatchOutcome(unit_id="u-1", text=payload, forced_finalization=True)],
        )

        result = outcome.results[0]
        assert result.is_trustworthy is True
        assert any("turn deadline" in item for item in result.limitations)
        assert outcome.produced_usable_evidence is True

    def test_an_unforced_worker_gains_no_limitation(self):
        import json

        from deerflow.dbtl.agent_selector import SelectionResult
        from deerflow.dbtl.stage_runner import DispatchOutcome, StageExecutionPlan, WorkUnit, collect_results
        from deerflow.dbtl.stage_spec import resolve_stage_spec

        spec = resolve_stage_spec("design")
        unit = WorkUnit(unit_id="u-1", capability="experimental_design", agent_name="general-purpose", prompt="p")
        plan = StageExecutionPlan(spec=spec, selection=SelectionResult(), units=(unit,))
        payload = json.dumps({"status": "completed", "summary": "Done."})

        outcome = collect_results(plan, [DispatchOutcome(unit_id="u-1", text=payload)])

        assert outcome.results[0].limitations == ()


class TestDegenerateBudgets:
    def test_a_reserve_larger_than_the_budget_still_leaves_one_call(self):
        """A tiny budget must not warn on the very first call and then stop.

        Clamping matters because the reserve is derived from a stage budget the
        caller owns, so a small ``max_turns`` can legitimately produce a reserve
        wider than the whole run.
        """
        middleware = FinalizationDeadlineMiddleware(max_model_calls=2, reserve_calls=8)
        runtime = _runtime()

        assert middleware._apply(_state(0), runtime) is None
        assert middleware.forced_finalization(runtime) is False
