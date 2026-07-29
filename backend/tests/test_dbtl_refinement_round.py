"""Answering the objection, rather than re-running the whole debate.

"Request changes" used to produce a fresh council on the original request. The
scientist's actual objection lived in a review rationale nobody read back, so
the second attempt argued the same points from the same starting position and
had no way to know what had been rejected — and the reviewer got a document that
did not address the one thing they said.

A refinement round is the same council pointed at one question. It carries the
objection verbatim, it is numbered so a reader can tell a second pass from a
first, and it is bounded so "request changes" cannot become an unbounded spend.
"""

from __future__ import annotations

import json

import pytest

from deerflow.agents.dbtl.stage_execution import (
    MAX_DESIGN_ROUNDS,
    _change_request,
    _design_round,
)


def _event(event_type: str, *, stage: str = "design", decision: str = "changes_requested", rationale: str = "", sequence: int = 1) -> dict:
    return {
        "sequence": sequence,
        "event_type": event_type,
        "actor_user_id": "user-1",
        "payload": {"stage": stage, "decision": decision, "rationale": rationale},
    }


class TestReadingTheObjection:
    def test_it_reads_the_latest_change_request(self):
        events = [
            _event("stage.reviewed", rationale="Two seasons is not enough.", sequence=1),
            _event("stage.reviewed", rationale="Still no rejection criterion.", sequence=2),
        ]

        assert _change_request(events) == "Still no rejection criterion."

    def test_it_reads_the_persisted_review_decision_value(self):
        events = [
            _event(
                "stage.reviewed",
                decision="request_changes",
                rationale="Keep the external site out of model selection.",
            )
        ]

        assert _change_request(events) == "Keep the external site out of model selection."

    def test_an_approval_clears_a_previous_objection(self):
        """An answered objection must not steer the next round.

        Otherwise a cycle that was approved and later re-opened for an unrelated
        reason would keep arguing about something already settled.
        """
        events = [
            _event("stage.reviewed", rationale="Two seasons is not enough.", sequence=1),
            _event("stage.reviewed", decision="approved", rationale="Good.", sequence=2),
        ]

        assert _change_request(events) is None

    def test_another_stage_s_objection_is_not_borrowed(self):
        events = [_event("stage.reviewed", stage="reconciliation", rationale="Units are wrong.")]

        assert _change_request(events) is None

    def test_a_rejection_is_not_a_change_request(self):
        # Reject ends the attempt; it is not "try again addressing this".
        events = [_event("stage.reviewed", decision="rejected", rationale="Wrong question entirely.")]

        assert _change_request(events) is None

    def test_an_empty_rationale_carries_nothing(self):
        events = [_event("stage.reviewed", rationale="   ")]

        assert _change_request(events) is None

    @pytest.mark.parametrize("events", [[], None, "nonsense", [{"event_type": "stage.reviewed"}], [None]])
    def test_a_malformed_feed_does_not_raise(self, events):
        # Read on every Design request; it must never be why one cannot run.
        assert _change_request(events) is None


class TestNumberingTheRound:
    def test_a_first_attempt_is_round_one(self):
        assert _design_round([]) == 1

    def test_each_change_request_opens_the_next_round(self):
        events = [
            _event("stage.reviewed", rationale="More detail.", sequence=1),
            _event("stage.reviewed", rationale="Still more.", sequence=2),
        ]

        assert _design_round(events) == 3

    def test_persisted_review_decisions_increment_the_round(self):
        events = [
            _event("stage.reviewed", decision="request_changes", rationale="More detail."),
        ]

        assert _design_round(events) == 2

    def test_an_approval_does_not_open_a_round(self):
        events = [_event("stage.reviewed", decision="approved", rationale="Good.")]

        assert _design_round(events) == 1

    def test_rounds_are_bounded(self):
        """ "Request changes" must not become an unbounded spend.

        The cap does not block the review — a person may keep rejecting — it
        stops the *numbering* from claiming a depth of debate that the budget
        never funded.
        """
        events = [_event("stage.reviewed", rationale=f"Again {index}.", sequence=index) for index in range(50)]

        assert _design_round(events) == MAX_DESIGN_ROUNDS


class TestTheRoundThatActuallyRuns:
    """The objection has to reach the workers, not just the context blob."""

    @staticmethod
    def _adapter(dispatcher, activity):
        from test_dbtl_live_stage_execution import FakeRepo, _cycle

        from deerflow.agents.dbtl.stage_execution import LiveStageAdapter
        from deerflow.dbtl.agent_selector import AgentCandidate
        from deerflow.dbtl.capabilities import Capability

        repo = FakeRepo(_cycle())
        repo.list_activity = lambda cycle_id, *, project_id: _resolved(activity)

        def candidates():
            return [
                AgentCandidate(name="general-purpose", capabilities=frozenset(), available=True, is_generalist=True),
                AgentCandidate(
                    name="quant-geneticist",
                    capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
                    available=True,
                ),
            ]

        roster = json.dumps(
            {
                "positions": [
                    {
                        "focus": "quantitative genetics",
                        "brief": "Argue from variance components.",
                        "agent_name": "quant-geneticist",
                        "capability": "quantitative_genetics",
                    }
                ]
            }
        )
        return LiveStageAdapter(
            repo=repo,
            app_config=None,
            candidate_provider=candidates,
            dispatcher=dispatcher,
            roster_writer=lambda _prompt: roster,
        )

    @pytest.mark.anyio
    async def test_the_objection_is_quoted_to_the_seats(self, tmp_path):
        from test_dbtl_live_stage_execution import FakeDispatcher, _runtime_config, _structured_result

        dispatcher = FakeDispatcher(text=_structured_result())
        objection = "There is still no rejection criterion."
        adapter = self._adapter(dispatcher, [_event("stage.reviewed", rationale=objection)])

        await adapter.execute(
            project_id="project-1",
            cycle_id="cycle-1",
            request_text="Design the drought trial.",
            state={},
            config=_runtime_config(tmp_path),
        )

        first_wave = dispatcher.calls[0][0]
        # Verbatim: a paraphrase is the failure mode this round exists to fix.
        assert objection in first_wave[0].prompt
        assert "round 2" in first_wave[0].prompt

    @pytest.mark.anyio
    async def test_the_seats_are_numbered_for_the_live_view(self, tmp_path):
        from test_dbtl_live_stage_execution import FakeDispatcher, _runtime_config, _structured_result

        dispatcher = FakeDispatcher(text=_structured_result())
        adapter = self._adapter(dispatcher, [_event("stage.reviewed", rationale="Not enough seasons.")])

        await adapter.execute(
            project_id="project-1",
            cycle_id="cycle-1",
            request_text="Design it.",
            state={},
            config=_runtime_config(tmp_path),
        )

        # Every wave of the round agrees on its number, so the debate panel
        # groups the whole round together rather than splitting the chair off.
        for units, _budget in dispatcher.calls:
            assert all(unit.round == 2 for unit in units)

    @pytest.mark.anyio
    async def test_a_first_pass_carries_no_objection(self, tmp_path):
        from test_dbtl_live_stage_execution import FakeDispatcher, _runtime_config, _structured_result

        dispatcher = FakeDispatcher(text=_structured_result())
        adapter = self._adapter(dispatcher, [])

        await adapter.execute(
            project_id="project-1",
            cycle_id="cycle-1",
            request_text="Design it.",
            state={},
            config=_runtime_config(tmp_path),
        )

        first_wave = dispatcher.calls[0][0]
        assert "asked for changes" not in first_wave[0].prompt
        assert all(unit.round == 1 for unit in first_wave)


async def _resolved(value):
    return value
