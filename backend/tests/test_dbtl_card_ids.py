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


class TestTheLengthLimitOpenAIEnforces:
    """The second provider constraint that becomes durable damage.

    Anthropic caps nothing here, so a request id embedding a full cycle id
    passed every local check and every Claude turn. OpenAI's Responses API
    rejects a ``call_id`` over 64 characters, so the first GPT turn *after* the
    card failed with ``Invalid 'input[N].call_id': string too long`` — 74
    characters, on an unrelated request, unrecoverable from inside the thread.
    """

    @pytest.mark.parametrize(
        "builder",
        [
            lambda decision, nonce: supervisor._council_preflight_message(decision, _plan(), _recommendation(), request_nonce=nonce),
            lambda decision, nonce: supervisor._council_adjustment_message(decision, request_nonce=nonce),
            lambda decision, nonce: supervisor._design_authoring_message(decision, note="n", question="q", request_nonce=nonce),
            lambda decision, nonce: supervisor._design_clarification_message(decision, note="n", question="q", request_nonce=nonce),
            lambda decision, nonce: supervisor._present_artifact_messages(decision, note="n", artifact_uri="/mnt/user-data/outputs/x.md", request_nonce=nonce),
        ],
    )
    def test_every_emitted_card_id_fits_the_limit(self, builder):
        """A realistic cycle id is a UUID; that is what overflowed."""
        from deerflow.dbtl.branches import BranchDecision, SupervisorBranch
        from deerflow.dbtl.routing import RouteKind, RouteSource, RoutingDecision

        decision = BranchDecision(
            branch=SupervisorBranch.CYCLE_CONTINUATION,
            route=RoutingDecision(kind=RouteKind.CYCLE_CONTINUATION, source=RouteSource.EXPLICIT_CHOICE),
            cycle_id="cycle-f5fad897-e43b-44b8-b2ae-7d9ba2f08fcf",
        )
        ai, _tool = builder(decision, "run-8d4e1f2a-0b6c-4a51-9f3d-7c2e5a1b8d90")
        call_id = ai.tool_calls[0]["id"]
        assert len(call_id) <= supervisor.MAX_CARD_REQUEST_ID_CHARS, call_id
        assert TOOL_USE_ID.match(call_id), call_id

    def test_the_id_still_distinguishes_two_cycles(self):
        first = supervisor.card_request_id(COUNCIL_PREFLIGHT_PREFIX, "cycle-aaaaaaaa-1111-2222-3333-444444444444", "run-1")
        second = supervisor.card_request_id(COUNCIL_PREFLIGHT_PREFIX, "cycle-aaaaaaaa-1111-2222-3333-555555555555", "run-1")
        # The readable head is identical after truncation, so only the digest
        # keeps these apart — which is exactly why it hashes the full id.
        assert first != second

    def test_the_id_is_stable_for_the_same_card(self):
        args = (COUNCIL_PREFLIGHT_PREFIX, "cycle-1", "run-1")
        assert supervisor.card_request_id(*args) == supervisor.card_request_id(*args)


def _plan():
    from deerflow.dbtl.agent_selector import AgentCandidate
    from deerflow.dbtl.capabilities import Capability
    from deerflow.dbtl.council import plan_council
    from deerflow.dbtl.stage_spec import resolve_stage_spec

    return plan_council(
        resolve_stage_spec("design", domain_profile="generic"),
        (AgentCandidate(name="designer", capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN})),),
        model="gpt-5.6-sol",
    )


def _recommendation():
    from deerflow.dbtl.council import recommend_depth

    return recommend_depth("Design the drought cycle.")
