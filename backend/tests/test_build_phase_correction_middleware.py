from __future__ import annotations

import json
from unittest.mock import MagicMock

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.build_phase_correction_middleware import BuildPhaseCorrectionMiddleware


def _runtime():
    runtime = MagicMock()
    runtime.context = {"thread_id": "thread-1", "run_id": "run-1"}
    return runtime


def _payload(*, status: str = "completed", done: bool = True, extra_checks: list[dict] | None = None) -> str:
    return json.dumps(
        {
            "status": status,
            "summary": "Implemented and checked the phase.",
            "artifact_refs": [],
            "claims": [],
            "evidence_refs": [],
            "limitations": [],
            "quality_checks": [
                {
                    "name": "phase_done_condition",
                    "passed": done,
                    "detail": "The execution check failed." if not done else "",
                },
                *(extra_checks or []),
            ],
            "recommended_next_actions": [],
            "provenance": {},
        }
    )


def _state(text: str, *, message_id: str = "result-1") -> dict:
    return {
        "messages": [
            HumanMessage(content="Build it."),
            AIMessage(id=message_id, content=text),
        ]
    }


def _middleware() -> BuildPhaseCorrectionMiddleware:
    return BuildPhaseCorrectionMiddleware(
        capability="software_and_workflow_engineering",
        agent_name="general-purpose",
    )


def test_correction_runs_at_agent_exit_not_ahead_of_native_model_guards() -> None:
    assert BuildPhaseCorrectionMiddleware.after_model is AgentMiddleware.after_model
    assert BuildPhaseCorrectionMiddleware.after_agent is not AgentMiddleware.after_agent


def test_failed_completion_check_gets_one_same_attempt_correction() -> None:
    middleware = _middleware()
    state = _state(_payload(done=False))

    update = middleware.after_agent(state, _runtime())

    assert update is not None
    assert update["jump_to"] == "model"
    assert len(update["messages"]) == 1
    reminder = update["messages"][0]
    assert isinstance(reminder, HumanMessage)
    assert reminder.additional_kwargs["hide_from_ui"] is True
    assert "phase_done_condition" in reminder.content
    assert "rerun" in reminder.content.lower()
    assert "unless the rerun actually passes" in reminder.content.lower()

    # The failed AI report stays in state so the shared deadline still counts
    # the model call that requested this correction.
    combined = [*state["messages"], *update["messages"]]
    assert sum(isinstance(message, AIMessage) for message in combined) == 1

    assert middleware.after_agent(_state(_payload(done=False), message_id="result-2"), _runtime()) is None


def test_failed_worker_status_can_recover_when_it_names_a_failed_check() -> None:
    middleware = _middleware()

    update = middleware.after_agent(_state(_payload(status="failed", done=False)), _runtime())

    assert update is not None and update["jump_to"] == "model"


def test_passing_or_non_gating_checks_do_not_open_a_correction_cycle() -> None:
    middleware = _middleware()
    rerun_only = _payload(
        done=True,
        extra_checks=[
            {
                "name": "seeded reproducibility",
                "passed": False,
                "detail": "A repeat run was not performed.",
            }
        ],
    )

    assert middleware.after_agent(_state(_payload(done=True)), _runtime()) is None
    assert middleware.after_agent(_state(rerun_only), _runtime()) is None


def test_failed_implementation_check_cannot_be_hidden_by_a_true_done_marker() -> None:
    middleware = _middleware()
    text = _payload(
        done=True,
        extra_checks=[
            {
                "name": "integration tests",
                "passed": False,
                "detail": "Two tests failed.",
            }
        ],
    )

    update = middleware.after_agent(_state(text), _runtime())

    assert update is not None
    assert "integration tests" in update["messages"][-1].content


def test_tool_calls_and_malformed_results_stay_with_their_existing_owners() -> None:
    middleware = _middleware()
    with_tool = {
        "messages": [
            HumanMessage(content="Build it."),
            AIMessage(
                content="",
                tool_calls=[{"name": "read_file", "args": {"path": "/mnt/user-data/x"}, "id": "call-1"}],
            ),
        ]
    }

    assert middleware.after_agent(with_tool, _runtime()) is None
    assert middleware.after_agent(_state("not json"), _runtime()) is None


def test_new_agent_invocation_gets_a_fresh_single_retry_budget() -> None:
    middleware = _middleware()
    runtime = _runtime()

    assert middleware.after_agent(_state(_payload(done=False)), runtime) is not None
    assert middleware.after_agent(_state(_payload(done=False), message_id="result-2"), runtime) is None

    middleware.before_agent(_state(_payload(done=False)), runtime)

    assert middleware.after_agent(_state(_payload(done=False), message_id="result-3"), runtime) is not None


def test_native_guard_or_forced_deadline_blocks_the_correction_jump() -> None:
    guarded_runtime = _runtime()
    guarded_runtime.context["stop_reason"] = "token_capped"
    assert _middleware().after_agent(_state(_payload(done=False)), guarded_runtime) is None

    forced = BuildPhaseCorrectionMiddleware(
        capability="software_and_workflow_engineering",
        agent_name="general-purpose",
        retry_blocked=lambda _runtime: True,
    )
    assert forced.after_agent(_state(_payload(done=False)), _runtime()) is None
