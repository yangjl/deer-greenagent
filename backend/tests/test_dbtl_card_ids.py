"""A card's tool-call id has to survive being sent back to the provider.

Every DBTL card is an ``ask_clarification`` AI-tool call plus its ToolMessage,
and both land in checkpoint history. The next model call replays that history,
and Anthropic validates ``tool_use.id`` against ``^[a-zA-Z0-9_-]+$``. A colon in
the id is accepted locally, written to the checkpoint, and then rejects **every
subsequent turn in the thread** with

    messages.0.content.0.tool_use.id: String should match pattern '^[a-zA-Z0-9_-]+$'

which is unrecoverable without starting a new conversation. The failure is also
maximally confusing: it surfaces one turn *after* the card that caused it, on an
unrelated request, naming message 0 rather than the card.

These tests pin the id grammar at the point of construction so a new card cannot
reintroduce it, and pin the prefix set against a subtler trap — the prefixes are
matched with ``startswith``, so one being a prefix of another silently routes a
card answer to the wrong branch.
"""

from __future__ import annotations

import re

import pytest

from deerflow.agents.dbtl import supervisor
from deerflow.agents.dbtl.supervisor import (
    COUNCIL_PREFLIGHT_PREFIX,
    DESIGN_AUTHORING_PREFIX,
    DESIGN_CLARIFICATION_PREFIX,
    PRESENT_ARTIFACT_PREFIX,
    SETUP_CLARIFICATION_PREFIX,
    SETUP_CONFIRMATION_PREFIX,
)

#: Anthropic's constraint on ``tool_use.id``. Reproduced rather than imported
#: because it is a provider contract, not a DeerFlow one.
TOOL_USE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")

ALL_PREFIXES = (
    SETUP_CLARIFICATION_PREFIX,
    SETUP_CONFIRMATION_PREFIX,
    DESIGN_CLARIFICATION_PREFIX,
    DESIGN_AUTHORING_PREFIX,
    COUNCIL_PREFLIGHT_PREFIX,
    PRESENT_ARTIFACT_PREFIX,
)


class TestThePrefixes:
    @pytest.mark.parametrize("prefix", ALL_PREFIXES)
    def test_a_prefix_is_a_legal_id_fragment(self, prefix):
        assert TOOL_USE_ID.match(prefix), f"{prefix!r} cannot appear in a tool_use id"

    def test_no_prefix_is_a_prefix_of_another(self):
        """``_card_answer`` matches with ``startswith``.

        If one prefix were a prefix of another, a reply to the longer card would
        also match the shorter one, and whichever branch checked first would
        consume an answer meant for a different question.
        """
        for outer in ALL_PREFIXES:
            for inner in ALL_PREFIXES:
                if outer is inner:
                    continue
                assert not outer.startswith(inner), f"{outer!r} starts with {inner!r}"


class TestEveryEmittedCard:
    """Built through the real constructors, with a realistic cycle id."""

    CYCLE = "cycle-8ae61f5b-a5f2-4800-bc11-08305d909a9d"
    NONCE = "run-0f2c9a11-4d3e-4a77-9e21-6b0c5f8d4e2a"

    def _decision(self):
        from deerflow.dbtl.branches import BranchDecision, SupervisorBranch
        from deerflow.dbtl.routing import RouteKind, RouteSource, RoutingDecision

        return BranchDecision(
            branch=SupervisorBranch.CYCLE_CONTINUATION,
            route=RoutingDecision(kind=RouteKind.CYCLE_CONTINUATION, source=RouteSource.EXPLICIT_CHOICE),
            cycle_id=self.CYCLE,
        )

    def _ids(self, messages) -> list[str]:
        ai, tool = messages
        return [
            *(call["id"] for call in ai.tool_calls),
            tool.tool_call_id,
            str(tool.id),
        ]

    def test_design_clarification_ids_are_legal(self):
        messages = supervisor._design_clarification_message(
            self._decision(),
            note="n",
            question="q",
            request_nonce=self.NONCE,
        )
        for value in self._ids(messages):
            assert TOOL_USE_ID.match(value), value

    def test_design_authoring_ids_are_legal(self):
        messages = supervisor._design_authoring_message(
            self._decision(),
            note="n",
            question="q",
            request_nonce=self.NONCE,
        )
        for value in self._ids(messages):
            assert TOOL_USE_ID.match(value), value

    def test_present_artifact_ids_are_legal(self):
        messages = supervisor._present_artifact_messages(
            self._decision(),
            note="n",
            artifact_uri="/mnt/user-data/outputs/dbtl/x/design/review.md",
            request_nonce=self.NONCE,
        )
        for value in self._ids(messages):
            assert TOOL_USE_ID.match(value), value

    def test_council_preflight_ids_are_legal(self):
        from deerflow.dbtl.agent_selector import AgentCandidate
        from deerflow.dbtl.capabilities import Capability
        from deerflow.dbtl.council import CouncilDepth, plan_council, recommend_depth
        from deerflow.dbtl.stage_spec import resolve_stage_spec

        plan = plan_council(
            resolve_stage_spec("design"),
            (AgentCandidate(name="designer", capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN})),),
            depth=CouncilDepth.MEDIUM,
            model="gpt-5.6-sol",
        )
        messages = supervisor._council_preflight_message(
            self._decision(),
            plan,
            recommend_depth("design a trial"),
            request_nonce=self.NONCE,
        )
        for value in self._ids(messages):
            assert TOOL_USE_ID.match(value), value

    def test_the_ai_message_id_is_not_itself_a_tool_use_id(self):
        """The paired AIMessage id may differ; only tool_use ids are constrained.

        Pinned so a future reader does not "fix" the message id and assume the
        constraint was about message ids generally.
        """
        ai, _tool = supervisor._design_clarification_message(
            self._decision(),
            note="n",
            question="q",
            request_nonce=self.NONCE,
        )
        assert ai.tool_calls[0]["id"] in str(ai.id)


class TestAnswersStillRoute:
    """Changing the id grammar must not break card-answer matching."""

    def test_an_authoring_answer_does_not_match_the_clarification_prefix(self):
        request_id = f"{DESIGN_AUTHORING_PREFIX}cycle-1__abc"

        assert request_id.startswith(DESIGN_AUTHORING_PREFIX)
        assert not request_id.startswith(DESIGN_CLARIFICATION_PREFIX)

    def test_a_confirmation_answer_does_not_match_the_setup_prefix(self):
        request_id = f"{SETUP_CONFIRMATION_PREFIX}abc"

        assert request_id.startswith(SETUP_CONFIRMATION_PREFIX)
        assert not request_id.startswith(SETUP_CLARIFICATION_PREFIX)
