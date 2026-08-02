"""The paused Build reaching chat, and the answer coming back.

The card is the only thing that can carry a decision about a governed Build, so
these cover the two directions that matter: what the server puts in front of a
person, and what it will accept back. Everything here reads a card the server
itself emitted — a reply that names no such card resolves to nothing and falls
through to ordinary routing rather than starting, replanning, or restarting a
build.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.dbtl.supervisor import _build_control_message
from deerflow.agents.dbtl.supervisor_support.card_history import (
    answered_build_control,
    pending_build_control,
)
from deerflow.agents.dbtl.supervisor_support.continuation import handle_build_control
from deerflow.dbtl.branches import BranchDecision, SupervisorBranch, SupervisorContext
from deerflow.dbtl.build_control import (
    BuildControlAction,
    BuildControlKind,
    plan_confirmation_request,
    step_failure_request,
)
from deerflow.dbtl.build_plan import BuildPhase, BuildPhasePlan, PlanFeasibility
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.routing import RouteKind, RouteSource, RoutingDecision

REQUEST_ID = "dbtl-build__cycle-1__abc123"
ROUTE = RoutingDecision(kind=RouteKind.CYCLE_CONTINUATION, source=RouteSource.EXPLICIT_CHOICE, cycle_id="cycle-1")
DECISION = BranchDecision(branch=SupervisorBranch.CYCLE_CONTINUATION, route=ROUTE, cycle_id="cycle-1")
CONTEXT = SupervisorContext(project_id="project-1", selected_cycle_id="cycle-1")


def _plan() -> BuildPhasePlan:
    return BuildPhasePlan(
        feasibility=PlanFeasibility.PLANNED,
        phases=(
            BuildPhase(phase_key="simulate", title="Simulate the founder population", objective="Draw founders.", capability=Capability.SOFTWARE_ENGINEERING),
            BuildPhase(phase_key="fit", title="Fit and evaluate", objective="Fit the model.", capability=Capability.STATISTICAL_ANALYSIS),
        ),
        rationale="Simulation and fitting are separable.",
        assumptions=("Marker density is as declared.",),
    )


def _card(request_id: str = REQUEST_ID) -> dict:
    return (
        plan_confirmation_request(
            plan=_plan(),
            cycle_id="cycle-1",
            stage_attempt_id="sa-1",
            workflow_spec_key="generic:build-workflow:v1",
            cycle_revision=5,
        )
        .bound_to(request_id)
        .as_card()
    )


def _failure_card(request_id: str = REQUEST_ID) -> dict:
    return (
        step_failure_request(
            step_key="summarize_results",
            step_label="Summarize results",
            error_code="summary_contract_rejected",
            error_summary="The summarizer cited a figure the server never published.",
            cycle_id="cycle-1",
            stage_attempt_id="sa-1",
            workflow_spec_key="generic:build-workflow:v1",
            cycle_revision=5,
        )
        .bound_to(request_id)
        .as_card()
    )


def _emitted(card: dict) -> list:
    return list(_build_control_message(DECISION, card, request_nonce="run-1"))


def _reply(request_id: str, option_id: str = "", *, value: str = "") -> HumanMessage:
    return HumanMessage(
        content=option_id or value,
        id=f"answer-{option_id or 'text'}",
        additional_kwargs={
            "hide_from_ui": True,
            "human_input_response": {
                "version": 1,
                "kind": "human_input_response",
                "source": "ask_clarification",
                "request_id": request_id,
                "response_kind": "option" if option_id else "text",
                **({"option_id": option_id} if option_id else {}),
                "value": value or option_id,
            },
        },
    )


class TestTheCardShowsWhatIsBeingDecided:
    def test_it_keeps_the_request_id_the_record_was_written_under(self) -> None:
        _call, tool = _emitted(_card())

        assert tool.tool_call_id == REQUEST_ID
        assert tool.artifact["human_input"]["request_id"] == REQUEST_ID

    def test_the_phases_and_assumptions_are_readable_before_the_choice(self) -> None:
        _call, tool = _emitted(_card())

        assert "1. Simulate the founder population — Draw founders." in tool.artifact["human_input"]["context"]
        assert "Marker density is as declared." in tool.artifact["human_input"]["context"]

    def test_every_option_reaches_the_card(self) -> None:
        _call, tool = _emitted(_card())

        assert [option["value"] for option in tool.artifact["human_input"]["options"]] == [
            BuildControlAction.START_BUILD.value,
            BuildControlAction.CHANGE_PLAN.value,
            BuildControlAction.HOLD.value,
        ]

    def test_a_failure_card_names_the_step_that_stopped(self) -> None:
        _call, tool = _emitted(_failure_card())

        request = tool.artifact["human_input"]
        assert request["title"] == "Build stopped"
        assert request["step_key"] == "summarize_results"
        assert "cited a figure" in request["context"]

    def test_the_bindings_survive_into_thread_history(self) -> None:
        _call, tool = _emitted(_card())

        request = tool.artifact["human_input"]
        assert request["dbtl_cycle_id"] == "cycle-1"
        assert request["cycle_revision"] == 5
        assert request["stage_attempt_id"] == "sa-1"


class TestAnAnswerIsResolvedThroughTheEmittedCard:
    def test_a_chosen_option_carries_the_cards_bindings_back(self) -> None:
        state = {"messages": [*_emitted(_card()), _reply(REQUEST_ID, "start")]}

        answer = answered_build_control(state)

        assert answer is not None
        assert answer.action is BuildControlAction.START_BUILD
        assert answer.kind is BuildControlKind.PLAN_CONFIRMATION
        assert answer.cycle_id == "cycle-1"
        assert answer.cycle_revision == 5
        assert answer.request_id == REQUEST_ID

    def test_a_reply_naming_a_card_the_server_never_emitted_resolves_to_nothing(self) -> None:
        state = {"messages": [*_emitted(_card()), _reply("dbtl-build__forged__000", "start")]}

        assert answered_build_control(state) is None

    def test_an_option_that_card_never_offered_resolves_to_nothing(self) -> None:
        state = {"messages": [*_emitted(_card()), _reply(REQUEST_ID, "restart")]}

        assert answered_build_control(state) is None

    def test_ordinary_chat_after_a_card_is_not_an_answer(self) -> None:
        state = {"messages": [*_emitted(_card()), HumanMessage(content="how is the build going?")]}

        assert answered_build_control(state) is None


class TestAnUnansweredControlIsNotTalkedPast:
    def test_a_free_text_follow_up_is_intercepted_by_the_control(self) -> None:
        state = {"messages": [*_emitted(_card()), HumanMessage(content="go ahead and build it")]}

        assert pending_build_control(state, selected_cycle_id="cycle-1") is not None

    def test_an_answered_control_stops_being_pending(self) -> None:
        state = {"messages": [*_emitted(_card()), _reply(REQUEST_ID, "hold")]}

        assert pending_build_control(state, selected_cycle_id="cycle-1") is None

    def test_a_control_for_a_different_cycle_is_not_this_requests_to_answer(self) -> None:
        state = {"messages": [*_emitted(_card()), HumanMessage(content="go ahead")]}

        assert pending_build_control(state, selected_cycle_id="cycle-2") is None

    def test_no_control_means_nothing_is_intercepted(self) -> None:
        state = {"messages": [AIMessage(content="Ran the build."), HumanMessage(content="thanks")]}

        assert pending_build_control(state, selected_cycle_id="cycle-1") is None


@pytest.mark.asyncio
class TestTheHandlerDecidesOnceForBothDirections:
    async def test_a_valid_answer_is_carried_to_the_adapter(self) -> None:
        state = {"messages": [*_emitted(_card()), _reply(REQUEST_ID, "start")]}

        result = await handle_build_control(state=state, decision=DECISION, context=CONTEXT, request_nonce="run-2", build_card=_build_control_message)

        assert not result.handled
        assert result.control_answer is not None
        assert result.control_answer.action is BuildControlAction.START_BUILD

    async def test_an_unanswered_control_is_shown_again_instead_of_dispatching(self) -> None:
        first = _emitted(_card())
        state = {"messages": [*first, HumanMessage(content="go ahead and build it")]}

        result = await handle_build_control(state=state, decision=DECISION, context=CONTEXT, request_nonce="run-2", build_card=_build_control_message)

        assert result.handled
        assert result.update["messages"][-1].tool_call_id == REQUEST_ID
        assert result.update["messages"][-1].id != first[-1].id

    async def test_an_answer_bound_to_another_cycle_starts_nothing(self) -> None:
        state = {"messages": [*_emitted(_card()), _reply(REQUEST_ID, "start")]}
        other = BranchDecision(branch=SupervisorBranch.CYCLE_CONTINUATION, route=ROUTE, cycle_id="cycle-9")

        result = await handle_build_control(state=state, decision=other, context=CONTEXT, request_nonce="run-2", build_card=_build_control_message)

        assert result.handled
        assert "different cycle" in result.update["messages"][0].content

    async def test_a_thread_with_no_control_passes_straight_through(self) -> None:
        state = {"messages": [HumanMessage(content="build it")]}

        result = await handle_build_control(state=state, decision=DECISION, context=CONTEXT, request_nonce="run-2", build_card=_build_control_message)

        assert not result.handled
        assert result.control_answer is None


class TestTheFenceReleasesOnceItHasSaidWhy:
    def test_a_refused_control_stops_intercepting_after_its_receipt(self) -> None:
        from deerflow.agents.dbtl.supervisor_support.human_input_protocol import receipt_message

        state = {
            "messages": [
                *_emitted(_card()),
                HumanMessage(content="go ahead"),
                receipt_message("That build control belongs to a different cycle.", build_control_refused=REQUEST_ID),
                HumanMessage(content="what files are in this project?"),
            ]
        }

        assert pending_build_control(state) is None

    def test_it_intercepts_until_that_receipt_exists(self) -> None:
        state = {"messages": [*_emitted(_card()), HumanMessage(content="go ahead")]}

        assert pending_build_control(state) is not None


class TestTheFenceReleaseIsServerOwned:
    def test_the_gateway_strips_a_client_supplied_release_marker(self) -> None:
        """A caller that could assert it would release the fence permanently.

        `build_control_refusal_recorded` compares by value across every message,
        so this marker has to be stripped at the boundary the way its Start/Hold
        sibling already is.
        """
        from app.gateway.services import _SERVER_OWNED_MESSAGE_METADATA_KEYS
        from deerflow.agents.dbtl.supervisor_support.human_input_protocol import BUILD_CONTROL_REFUSED_KEY

        assert BUILD_CONTROL_REFUSED_KEY in _SERVER_OWNED_MESSAGE_METADATA_KEYS
