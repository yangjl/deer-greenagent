"""A roster proposed for the task, validated before anyone is seated.

The capability enum is twelve fixed, breeding-flavoured values, and selection
over it has one failure mode this project hits constantly: a deployment that
registers no specialists gets ``general-purpose`` for every seat, running the
same prompt lineage three times and calling the result a debate. Naming the
seats for the *question* fixes that even when every seat is the same agent,
because three distinct briefs argue from three distinct places.

What must not follow is a roster the model can invent freely. An agent nobody
registered, or a model nobody configured, has to be refused rather than quietly
swapped for the generalist — that silent swap is the original bug wearing a new
name. So parsing is fail-closed per seat, and it says why.
"""

from __future__ import annotations

import json

import pytest

from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.council_proposal import (
    MAX_PROPOSED_POSITIONS,
    build_proposal_prompt,
    parse_council_proposal,
)

KNOWN_AGENTS = ("general-purpose", "quant-geneticist")
KNOWN_MODELS = ("gpt-5.6-sol", "claude-sonnet-5")


def _seat(**overrides) -> dict:
    seat = {
        "focus": "quantitative genetics",
        "brief": "Argue from variance components and expected selection response.",
        "agent_name": "quant-geneticist",
        "model": "gpt-5.6-sol",
        "capability": "quantitative_genetics",
    }
    seat.update(overrides)
    return seat


def _payload(*seats: dict, chair: dict | None = None) -> str:
    body: dict = {"positions": list(seats)}
    if chair is not None:
        body["chair"] = chair
    return json.dumps(body)


def _parse(text: str, *, max_positions: int = 2):
    return parse_council_proposal(
        text,
        known_agents=KNOWN_AGENTS,
        known_models=KNOWN_MODELS,
        max_positions=max_positions,
    )


class TestAcceptingAProposal:
    def test_a_well_formed_seat_survives(self):
        proposal = _parse(_payload(_seat()))

        assert len(proposal.positions) == 1
        seat = proposal.positions[0]
        assert seat.focus == "quantitative genetics"
        assert seat.agent_name == "quant-geneticist"
        assert seat.model == "gpt-5.6-sol"
        assert seat.capability is Capability.QUANTITATIVE_GENETICS
        assert proposal.usable is True

    def test_distinct_briefs_are_what_make_it_a_debate(self):
        proposal = _parse(
            _payload(
                _seat(focus="quantitative genetics"),
                _seat(focus="field logistics", brief="Argue from what three sites can actually plant and measure."),
            )
        )

        briefs = {seat.brief for seat in proposal.positions}
        assert len(briefs) == 2

    def test_identical_briefs_collapse_to_one_seat(self):
        """Two seats arguing the same thing are one seat and twice the bill."""
        proposal = _parse(_payload(_seat(), _seat()))

        assert len(proposal.positions) == 1
        assert any("duplicate" in note.lower() for note in proposal.notes)

    def test_the_chair_may_be_named_too(self):
        proposal = _parse(_payload(_seat(), chair=_seat(agent_name="general-purpose", model="claude-sonnet-5")))

        assert proposal.chair is not None
        assert proposal.chair.agent_name == "general-purpose"
        assert proposal.chair.model == "claude-sonnet-5"

    def test_prose_around_the_object_is_tolerated(self):
        proposal = _parse(f"Here is my roster:\n```json\n{_payload(_seat())}\n```\nThat should do it.")

        assert len(proposal.positions) == 1


class TestRefusing:
    def test_an_unregistered_agent_is_refused_not_downgraded(self):
        proposal = _parse(_payload(_seat(agent_name="imaginary-expert")))

        assert proposal.positions == ()
        assert any("imaginary-expert" in reason for reason in proposal.rejected)
        # The specific thing that must never happen: falling back to the
        # generalist would reproduce the undisclosed monologue exactly.
        assert not any(seat.agent_name == "general-purpose" for seat in proposal.positions)

    def test_an_unconfigured_model_is_refused(self):
        proposal = _parse(_payload(_seat(model="gpt-9-imaginary")))

        assert proposal.positions == ()
        assert any("gpt-9-imaginary" in reason for reason in proposal.rejected)

    def test_an_omitted_model_is_allowed_and_means_inherit(self):
        proposal = _parse(_payload(_seat(model=None)))

        assert len(proposal.positions) == 1
        assert proposal.positions[0].model is None

    def test_an_unknown_capability_is_refused(self):
        proposal = _parse(_payload(_seat(capability="vibes")))

        assert proposal.positions == ()
        assert any("vibes" in reason for reason in proposal.rejected)

    def test_a_seat_with_no_brief_is_refused(self):
        """A seat with no angle is the generic prompt with extra steps."""
        proposal = _parse(_payload(_seat(brief="  ")))

        assert proposal.positions == ()

    def test_one_bad_seat_does_not_discard_the_good_ones(self):
        proposal = _parse(
            _payload(
                _seat(),
                _seat(focus="field logistics", brief="Sites and labour.", agent_name="nope"),
            )
        )

        assert len(proposal.positions) == 1
        assert proposal.rejected


class TestBounds:
    def test_positions_are_capped_at_the_depth_the_human_chose(self):
        seats = [_seat(focus=f"angle {index}", brief=f"Argue from angle {index}.") for index in range(6)]
        proposal = _parse(_payload(*seats), max_positions=2)

        assert len(proposal.positions) == 2
        assert any("capped" in note.lower() for note in proposal.notes)

    def test_the_hard_ceiling_holds_even_if_a_caller_asks_for_more(self):
        seats = [_seat(focus=f"angle {index}", brief=f"Argue from angle {index}.") for index in range(40)]
        proposal = _parse(_payload(*seats), max_positions=99)

        assert len(proposal.positions) <= MAX_PROPOSED_POSITIONS


class TestDegrading:
    @pytest.mark.parametrize("text", ["", "   ", "I could not decide.", "{not json}", "[]", "null"])
    def test_an_unusable_reply_degrades_instead_of_raising(self, text):
        """A malformed roster must cost the deterministic council, not the cycle.

        The caller falls back to capability selection, which is exactly what
        ran before proposals existed — a worse council, not a failed one.
        """
        proposal = _parse(text)

        assert proposal.usable is False
        assert proposal.positions == ()

    def test_a_proposal_with_only_rejected_seats_is_not_usable(self):
        proposal = _parse(_payload(_seat(agent_name="nope")))

        assert proposal.usable is False


class TestThePrompt:
    def test_it_names_the_agents_that_may_be_used(self):
        prompt = build_proposal_prompt(
            request_text="Design a drought trial.",
            stage_context="{}",
            known_agents=KNOWN_AGENTS,
            known_models=KNOWN_MODELS,
            max_positions=2,
        )

        for agent in KNOWN_AGENTS:
            assert agent in prompt
        # Listing them is what makes the fail-closed parse a fair rule rather
        # than a trap: a model that was never shown the roster cannot be
        # blamed for guessing at it.
        assert "2" in prompt

    def test_it_asks_for_distinct_angles_rather_than_a_headcount(self):
        prompt = build_proposal_prompt(
            request_text="Design a drought trial.",
            stage_context="{}",
            known_agents=KNOWN_AGENTS,
            known_models=KNOWN_MODELS,
            max_positions=4,
        )

        assert "disagree" in prompt.lower()


class TestReachingDispatch:
    """A roster nobody dispatches is a document, not a council."""

    @staticmethod
    def _adapter(tmp_path, dispatcher, reply: str):
        from test_dbtl_live_stage_execution import FakeRepo, _cycle

        from deerflow.agents.dbtl.stage_execution import LiveStageAdapter
        from deerflow.dbtl.agent_selector import AgentCandidate

        def candidates():
            return [
                AgentCandidate(name="general-purpose", capabilities=frozenset(), available=True, is_generalist=True),
                AgentCandidate(
                    name="quant-geneticist",
                    capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
                    available=True,
                ),
            ]

        return LiveStageAdapter(
            repo=FakeRepo(_cycle()),
            app_config=None,
            candidate_provider=candidates,
            dispatcher=dispatcher,
            roster_writer=lambda _prompt: reply,
        )

    @pytest.mark.anyio
    async def test_the_proposed_seats_are_the_units_that_run(self, tmp_path):
        from test_dbtl_live_stage_execution import FakeDispatcher, _runtime_config, _structured_result

        dispatcher = FakeDispatcher(text=_structured_result())
        reply = _payload(
            _seat(focus="quantitative genetics", agent_name="quant-geneticist", model=None),
            _seat(
                focus="field logistics",
                brief="Argue from what three sites can plant and measure.",
                agent_name="general-purpose",
                model=None,
                capability="field_trial_quality_control",
            ),
        )
        adapter = self._adapter(tmp_path, dispatcher, reply)

        await adapter.execute(
            project_id="project-1",
            cycle_id="cycle-1",
            request_text="Design the drought trial.",
            state={},
            config=_runtime_config(tmp_path),
        )

        first_wave = dispatcher.calls[0][0]
        assert [unit.agent_name for unit in first_wave] == ["quant-geneticist", "general-purpose"]
        # The briefs are the point: identical prompts to identical agents
        # produce corroboration, not debate.
        assert len({unit.prompt for unit in first_wave}) == 2
        assert "field logistics" in first_wave[1].prompt

    @pytest.mark.anyio
    async def test_an_unusable_roster_falls_back_to_selection(self, tmp_path):
        from test_dbtl_live_stage_execution import FakeDispatcher, _runtime_config, _structured_result

        dispatcher = FakeDispatcher(text=_structured_result())
        adapter = self._adapter(tmp_path, dispatcher, "I could not decide.")

        await adapter.execute(
            project_id="project-1",
            cycle_id="cycle-1",
            request_text="Design the drought trial.",
            state={},
            config=_runtime_config(tmp_path),
        )

        # A worse council, not a failed one.
        assert dispatcher.calls
        assert dispatcher.calls[0][0]

    @pytest.mark.anyio
    async def test_a_raising_writer_never_costs_the_stage(self, tmp_path):
        from test_dbtl_live_stage_execution import FakeDispatcher, _runtime_config, _structured_result

        def explode(_prompt):
            raise RuntimeError("provider is down")

        from test_dbtl_live_stage_execution import FakeRepo, _cycle

        from deerflow.agents.dbtl.stage_execution import LiveStageAdapter
        from deerflow.dbtl.agent_selector import AgentCandidate

        dispatcher = FakeDispatcher(text=_structured_result())
        adapter = LiveStageAdapter(
            repo=FakeRepo(_cycle()),
            app_config=None,
            candidate_provider=lambda: [
                AgentCandidate(name="general-purpose", capabilities=frozenset(), available=True, is_generalist=True),
            ],
            dispatcher=dispatcher,
            roster_writer=explode,
        )

        result = await adapter.execute(
            project_id="project-1",
            cycle_id="cycle-1",
            request_text="Design the drought trial.",
            state={},
            config=_runtime_config(tmp_path),
        )

        assert dispatcher.calls
        assert result.artifact_uri


class TestWhoMayHoldASeat:
    """A council seat argues a position; not every registered agent can.

    ``bash`` is a genuinely registered subagent, so the fail-closed agent check
    accepted it — and a live council seated it as "Independent position:
    simulation implementation and reproducibility". It spent its whole turn
    budget running commands and returned no argument, because an execution
    specialist has no position to take. The agent list was constraining *which*
    names may be used without asking whether they can debate at all.
    """

    def test_an_execution_specialist_is_not_eligible(self):
        from deerflow.dbtl.council_proposal import is_deliberative_agent

        assert not is_deliberative_agent("bash")

    def test_the_generalist_and_custom_specialists_remain_eligible(self):
        from deerflow.dbtl.council_proposal import is_deliberative_agent

        assert is_deliberative_agent("general-purpose")
        assert is_deliberative_agent("quant-geneticist")

    def test_eligibility_ignores_case_and_padding(self):
        from deerflow.dbtl.council_proposal import is_deliberative_agent

        assert not is_deliberative_agent("  BASH ")

    def test_seatable_agents_filters_a_candidate_list(self):
        from deerflow.dbtl.council_proposal import seatable_agents

        assert seatable_agents(("general-purpose", "bash", "designer")) == (
            "general-purpose",
            "designer",
        )

    def test_a_proposal_naming_an_ineligible_agent_is_refused_by_name(self):
        """Refused with a reason, not silently swapped for the generalist.

        The silent swap is the original bug arriving through the feature meant
        to fix it, and a seat that was asked for and refused is otherwise
        indistinguishable from one never considered.
        """
        proposal = parse_council_proposal(
            _payload(
                _seat(agent_name="bash", focus="reproducibility"),
                _seat(focus="variance"),
                chair=_seat(focus="synthesis", agent_name="general-purpose", capability="experimental_design"),
            ),
            known_agents=(*KNOWN_AGENTS, "bash"),
            known_models=KNOWN_MODELS,
            max_positions=3,
        )

        assert any("bash" in reason for reason in proposal.rejected)
        # One bad seat does not discard the good ones.
        assert [seat.focus for seat in proposal.positions] == ["variance"]

    def test_an_ineligible_chair_is_refused_too(self):
        proposal = parse_council_proposal(
            _payload(
                _seat(focus="variance"),
                chair=_seat(agent_name="bash", focus="synthesis"),
            ),
            known_agents=(*KNOWN_AGENTS, "bash"),
            known_models=KNOWN_MODELS,
            max_positions=2,
        )

        assert proposal.chair is None
        assert any("bash" in reason for reason in proposal.rejected)

    def test_the_prompt_never_offers_an_ineligible_agent(self):
        """Cheaper to not ask than to refuse: the model cannot pick what it
        was never shown, and the refusal path stays a backstop."""
        from deerflow.dbtl.council_proposal import build_proposal_prompt

        prompt = build_proposal_prompt(
            request_text="Design a drought trial.",
            stage_context="",
            known_agents=("general-purpose", "bash", "designer"),
            known_models=KNOWN_MODELS,
            max_positions=2,
        )

        agent_line = next(line for line in prompt.splitlines() if "general-purpose" in line)
        assert "bash" not in agent_line
        assert "designer" in agent_line
