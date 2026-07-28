"""The participant cards on the design-meeting preflight, and what they change.

The preflight used to be take-it-or-redraw-it: a person could set a depth or
send the roster back to the writer, but could not say "this seat, on that
model, with this budget, and push hardest on X". The card now carries one
editable entry per participant — model, token budget, reasoning strength, and
owner instructions — prefilled with the roster writer's suggestions. These
tests pin the three promises that make that safe:

* the prefills are the plan, so an untouched card changes nothing;
* edits are validated server-side field by field, and recovered from the card
  the server itself emitted, scoped to the answering turn;
* an owner's instructions reach the worker prompt verbatim, but the writer's
  own suggestion echoed back untouched is never attributed to the owner.
"""

from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from test_dbtl_supervisor_graph import FULL_STATE, SCHEMA, fake_lead_agent

from deerflow.agents.dbtl.supervisor import SupervisorContext, build_supervisor_graph
from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.council import plan_council
from deerflow.dbtl.council_settings import (
    MAX_INSTRUCTION_CHARS,
    MAX_PARTICIPANT_TOKENS,
    MIN_PARTICIPANT_TOKENS,
    ParticipantSettings,
    apply_participant_settings,
    parse_participant_settings,
    participant_id_for_seat,
    participants_payload,
)
from deerflow.dbtl.stage_spec import resolve_stage_spec

KNOWN_MODELS = ("gpt-5.6-sol", "claude-fable-5")


def _plan(attempt_id: str = "council"):
    return plan_council(
        resolve_stage_spec("design", domain_profile="generic"),
        (AgentCandidate(name="designer", capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN})),),
        model="gpt-5.6-sol",
        attempt_id=attempt_id,
    )


class TestParticipantIds:
    def test_preview_and_dispatch_seats_share_one_id(self):
        """Preview and dispatch mint different attempt prefixes; edits must
        apply to both, so the id is the role-shaped tail they share."""
        assert participant_id_for_seat("preview-position-1") == "position-1"
        assert participant_id_for_seat("dbtl-ab12cd34-position-2") == "position-2"
        assert participant_id_for_seat("preview-red-team") == "red-team"
        assert participant_id_for_seat("dbtl-ab12cd34-chair") == "chair"


class TestParsingEdits:
    def test_valid_edits_survive(self):
        parsed = parse_participant_settings(
            {
                "position-1": {
                    "model": "claude-fable-5",
                    "max_tokens": 200_000,
                    "reasoning": "extended",
                    "instructions": "Compare against last season's controls.",
                }
            },
            known_models=KNOWN_MODELS,
        )
        assert parsed["position-1"] == ParticipantSettings(
            participant_id="position-1",
            model="claude-fable-5",
            max_tokens=200_000,
            reasoning="extended",
            instructions="Compare against last season's controls.",
        )

    def test_an_unknown_model_is_dropped_while_the_other_edits_survive(self):
        """Fail-soft per field: refusing the payload for one stale field would
        discard the instructions the person typed."""
        parsed = parse_participant_settings(
            {"chair": {"model": "gpt-nobody-configured", "max_tokens": 50_000, "instructions": "Weigh the red team heavily."}},
            known_models=KNOWN_MODELS,
        )
        assert parsed["chair"].model is None
        assert parsed["chair"].max_tokens == 50_000
        assert parsed["chair"].instructions == "Weigh the red team heavily."

    def test_token_budgets_are_clamped_into_bounds(self):
        parsed = parse_participant_settings(
            {"position-1": {"max_tokens": 5}, "position-2": {"max_tokens": 10**9}},
            known_models=KNOWN_MODELS,
        )
        assert parsed["position-1"].max_tokens == MIN_PARTICIPANT_TOKENS
        assert parsed["position-2"].max_tokens == MAX_PARTICIPANT_TOKENS

    def test_junk_is_dropped_without_raising(self):
        parsed = parse_participant_settings(
            {
                "not-a-participant": {"max_tokens": 50_000},
                "position-1": {"reasoning": "galaxy-brain", "max_tokens": "not a number"},
                "red-team": "not a mapping",
                "chair": {"instructions": "x" * (MAX_INSTRUCTION_CHARS + 100)},
            },
            known_models=KNOWN_MODELS,
        )
        assert "not-a-participant" not in parsed
        assert "position-1" not in parsed, "a participant with no surviving edit is no edit"
        assert "red-team" not in parsed
        assert len(parsed["chair"].instructions) == MAX_INSTRUCTION_CHARS

    def test_a_non_mapping_payload_is_empty(self):
        assert parse_participant_settings(["nope"], known_models=KNOWN_MODELS) == {}
        assert parse_participant_settings(None, known_models=KNOWN_MODELS) == {}


class TestApplyingEditsToThePlan:
    def test_edits_land_on_the_right_seats_and_the_original_is_untouched(self):
        plan = _plan()
        edited = apply_participant_settings(
            plan,
            {
                "position-1": ParticipantSettings(participant_id="position-1", model="claude-fable-5", max_tokens=120_000),
                "chair": ParticipantSettings(participant_id="chair", reasoning="extended"),
            },
        )
        position = edited.seat("council-position-1")
        assert position.model == "claude-fable-5"
        assert position.max_tokens == 120_000
        assert edited.seat("council-chair").reasoning == "extended"
        assert edited.seat("council-red-team").model == "gpt-5.6-sol"
        # The plan the preflight showed must not change under the caller.
        assert plan.seat("council-position-1").model == "gpt-5.6-sol"
        assert plan.seat("council-position-1").max_tokens is None

    def test_an_untouched_instructions_prefill_is_not_recorded_as_the_owners(self):
        """The card prefills instructions with the seat's brief. Echoed back
        byte-identical, it is the writer's text, not the owner's."""
        plan = _plan()
        brief = plan.seat("council-position-1").brief
        edited = apply_participant_settings(
            plan,
            {"position-1": ParticipantSettings(participant_id="position-1", instructions=brief)},
        )
        assert edited.seat("council-position-1").instructions == ""

    def test_no_settings_returns_the_same_plan(self):
        plan = _plan()
        assert apply_participant_settings(plan, None) is plan
        assert apply_participant_settings(plan, {}) is plan


class TestTheCardPayload:
    def test_prefills_are_the_plan(self):
        plan = _plan()
        payload = participants_payload(plan, model_options=KNOWN_MODELS)
        assert [entry["id"] for entry in payload] == ["position-1", "red-team", "chair"]
        first = payload[0]
        assert first["model"] == "gpt-5.6-sol"
        assert first["model_options"] == list(KNOWN_MODELS)
        assert first["max_tokens"] == plan.budget.max_tokens
        assert first["reasoning"] == "standard"
        assert first["instructions"] == plan.seats[0].brief
        assert first["agent_name"] == "designer"

    def test_the_settings_ride_on_the_recorded_plan(self):
        """The review package records ``plan.as_dict()``; an edited dial that
        did not appear there would misreport what ran."""
        edited = apply_participant_settings(
            _plan(),
            {"position-1": ParticipantSettings(participant_id="position-1", max_tokens=90_000, reasoning="extended", instructions="Use the 2025 trials only.")},
        )
        seat = edited.as_dict()["seats"][0]
        assert seat["max_tokens"] == 90_000
        assert seat["reasoning"] == "extended"
        assert seat["instructions"] == "Use the 2025 trials only."


# ---------------------------------------------------------------------------
# The card round trip through the supervisor.
# ---------------------------------------------------------------------------


def _adapter(executed: list):
    class Adapter:
        def known_models(self):
            return KNOWN_MODELS

        async def preview_council(self, **kwargs):
            return _plan()

        async def execute(self, **kwargs):
            executed.append(kwargs)
            return SimpleNamespace(
                stage="design",
                cycle_id="cyc-1",
                note="ran",
                artifact_uri=None,
                clarification_question=None,
                authoring_request=None,
                produced_usable_evidence=True,
            )

    return Adapter()


def _graph(executed: list):
    return build_supervisor_graph(
        lead_agent=fake_lead_agent([]),
        context=SupervisorContext(project_id="proj-1", project_name="G2F", selected_cycle_id="cyc-1"),
        stage_adapter=_adapter(executed),
        state_schema=SCHEMA,
    ).compile(checkpointer=InMemorySaver())


def _preflight_card(executed: list):
    from deerflow.agents.dbtl import supervisor
    from deerflow.dbtl.branches import BranchDecision, SupervisorBranch
    from deerflow.dbtl.council import recommend_depth
    from deerflow.dbtl.routing import RouteKind, RouteSource, RoutingDecision

    decision = BranchDecision(
        branch=SupervisorBranch.CYCLE_CONTINUATION,
        route=RoutingDecision(kind=RouteKind.CYCLE_CONTINUATION, source=RouteSource.EXPLICIT_CHOICE),
        cycle_id="cyc-1",
    )
    return supervisor._council_preflight_message(
        decision,
        _plan(),
        recommend_depth("Design the drought cycle."),
        request_nonce="run-1",
        model_options=KNOWN_MODELS,
    )


def _answer(request_id: str, value: str, participants: dict | None = None) -> HumanMessage:
    return HumanMessage(
        content=f"Selected: {value}",
        id=f"answer-{sha256(f'{request_id}:{value}'.encode()).hexdigest()[:12]}",
        additional_kwargs={
            "hide_from_ui": True,
            "human_input_response": {
                "version": 1,
                "kind": "human_input_response",
                "source": "ask_clarification",
                "request_id": request_id,
                "response_kind": "option",
                "option_id": value,
                "value": value,
                **({"participants": participants} if participants is not None else {}),
            },
        },
    )


def _answered_state(value: str, participants: dict | None = None, *, request_id: str | None = None):
    ai, tool = _preflight_card([])
    card_id = ai.tool_calls[0]["id"]
    return {
        **FULL_STATE,
        "messages": [
            HumanMessage(content="Design the drought cycle.", id="h-1"),
            ai,
            tool,
            _answer(request_id or card_id, value, participants),
        ],
    }


EDITS = {
    "position-1": {"model": "claude-fable-5", "max_tokens": 150_000, "reasoning": "extended", "instructions": "Anchor on the 2025 drought trials."},
    "chair": {"model": "gpt-made-up", "max_tokens": 80_000},
}


class TestTheCardCarriesTheEditor:
    def test_the_preflight_artifact_lists_editable_participants(self):
        _ai, tool = _preflight_card([])
        request = tool.artifact["human_input"]
        participants = request["council_participants"]
        assert [entry["id"] for entry in participants] == ["position-1", "red-team", "chair"]
        assert participants[0]["model_options"] == list(KNOWN_MODELS)
        assert participants[0]["instructions"], "the writer's suggestion must prefill the box"


class TestTheEditsReachTheMeeting:
    @pytest.mark.asyncio
    async def test_the_server_emitted_roster_reaches_dispatch(self):
        executed: list = []

        await _graph(executed).ainvoke(
            _answered_state("light"),
            config={"configurable": {"thread_id": "approved-roster"}},
        )

        assert executed, "the meeting never ran"
        proposal = executed[0]["approved_council_proposal"]
        assert [seat.focus for seat in proposal.positions] == [
            "experimental design",
        ]
        assert proposal.chair is not None
        assert proposal.chair.focus == "synthesis"

    @pytest.mark.asyncio
    async def test_edits_on_the_reply_reach_dispatch_validated(self):
        executed: list = []

        await _graph(executed).ainvoke(
            _answered_state("light", EDITS),
            config={"configurable": {"thread_id": "edits-1"}},
        )

        assert executed, "the meeting never ran"
        settings = executed[0]["participant_settings"]
        assert settings["position-1"].model == "claude-fable-5"
        assert settings["position-1"].max_tokens == 150_000
        assert settings["position-1"].reasoning == "extended"
        assert settings["position-1"].instructions == "Anchor on the 2025 drought trials."
        # The made-up model was dropped server-side; the budget survived.
        assert settings["chair"].model is None
        assert settings["chair"].max_tokens == 80_000

    def test_a_chair_resume_recovers_the_approved_roster_and_dials(self):
        """The clarification answer is newer than the preflight reply, but it
        is still the same meeting and must keep that chair."""
        from deerflow.agents.dbtl import supervisor
        from deerflow.dbtl.branches import BranchDecision, SupervisorBranch
        from deerflow.dbtl.routing import RouteKind, RouteSource, RoutingDecision

        state = _answered_state(
            "light",
            {
                "chair": {
                    "model": "claude-fable-5",
                    "max_tokens": 81_000,
                    "reasoning": "extended",
                }
            },
        )
        decision = BranchDecision(
            branch=SupervisorBranch.CYCLE_CONTINUATION,
            route=RoutingDecision(kind=RouteKind.CYCLE_CONTINUATION, source=RouteSource.EXPLICIT_CHOICE),
            cycle_id="cyc-1",
        )
        ai, tool = supervisor._design_clarification_message(
            decision,
            note="The chair paused.",
            question="Toy benchmark or credible simulator?",
            request_nonce="resume-1",
        )
        state["messages"].extend(
            [
                ai,
                tool,
                _answer(ai.tool_calls[0]["id"], "A credible simulator."),
            ]
        )

        proposal, settings = supervisor._resumed_council_setup(state, KNOWN_MODELS)

        assert proposal is not None
        assert proposal.chair is not None
        assert proposal.chair.model == "gpt-5.6-sol"
        assert settings["chair"].model == "claude-fable-5"
        assert settings["chair"].max_tokens == 81_000
        assert settings["chair"].reasoning == "extended"

    @pytest.mark.asyncio
    async def test_an_untouched_reply_sends_no_settings(self):
        executed: list = []

        await _graph(executed).ainvoke(
            _answered_state("light"),
            config={"configurable": {"thread_id": "edits-none"}},
        )

        assert executed
        assert "participant_settings" not in executed[0]

    @pytest.mark.asyncio
    async def test_a_forged_request_id_matches_nothing(self):
        executed: list = []

        await _graph(executed).ainvoke(
            _answered_state("light", EDITS, request_id="dbtl-council__forged__deadbeef"),
            config={"configurable": {"thread_id": "edits-forged"}},
        )

        assert executed
        assert "participant_settings" not in executed[0]

    @pytest.mark.asyncio
    async def test_a_later_ordinary_request_does_not_inherit_the_edits(self):
        """Like the depth: the answer stays in history, the edits apply to the
        meeting that reply convened and to nothing after it."""
        executed: list = []
        state = _answered_state("light", EDITS)
        state["messages"] = [*state["messages"], HumanMessage(content="continue the design stage", id="h-2")]

        await _graph(executed).ainvoke(
            state,
            config={"configurable": {"thread_id": "edits-later"}},
        )

        assert executed
        assert "participant_settings" not in executed[0]


class TestTheEditsReachTheWorkers:
    def test_proposed_units_carry_the_owners_dials(self):
        from deerflow.agents.dbtl.stage_execution import _proposed_units
        from deerflow.dbtl.council_proposal import CouncilProposal, ProposedSeat

        proposal = CouncilProposal(
            positions=(
                ProposedSeat(focus="statistics", brief="Argue from the trial statistics.", agent_name="general-purpose", capability=Capability.EXPERIMENTAL_DESIGN),
                ProposedSeat(focus="agronomy", brief="Argue from field agronomy.", agent_name="general-purpose", capability=Capability.EXPERIMENTAL_DESIGN),
            ),
            chair=ProposedSeat(focus="chair", brief="Weigh the positions.", agent_name="general-purpose", capability=Capability.EXPERIMENTAL_DESIGN),
        )
        settings = {
            "position-1": ParticipantSettings(participant_id="position-1", model="claude-fable-5", max_tokens=90_000, reasoning="extended", instructions="Anchor on the 2025 drought trials."),
        }
        units = _proposed_units(
            proposal,
            resolve_stage_spec("design", domain_profile="generic"),
            attempt_id="dbtl-x",
            context="{}",
            settings=settings,
        )
        assert units[0].model == "claude-fable-5"
        assert units[0].max_tokens == 90_000
        assert units[0].reasoning == "extended"
        assert "Anchor on the 2025 drought trials." in units[0].prompt
        assert "quoted exactly" in units[0].prompt
        # The untouched seat keeps the proposal's dials.
        assert units[1].model is None
        assert units[1].max_tokens is None
        assert "quoted exactly" not in units[1].prompt

    def test_an_instructions_prefill_echoed_back_is_not_quoted_as_the_owners(self):
        from deerflow.agents.dbtl.stage_execution import _proposed_units
        from deerflow.dbtl.council_proposal import CouncilProposal, ProposedSeat

        brief = "Argue from the trial statistics."
        proposal = CouncilProposal(
            positions=(ProposedSeat(focus="statistics", brief=brief, agent_name="general-purpose", capability=Capability.EXPERIMENTAL_DESIGN),),
            chair=ProposedSeat(focus="chair", brief="Weigh.", agent_name="general-purpose", capability=Capability.EXPERIMENTAL_DESIGN),
        )
        units = _proposed_units(
            proposal,
            resolve_stage_spec("design", domain_profile="generic"),
            attempt_id="dbtl-x",
            context="{}",
            settings={"position-1": ParticipantSettings(participant_id="position-1", instructions=brief)},
        )
        assert "quoted exactly" not in units[0].prompt


class TestWorkersCanActuallyOpenWhatTheManifestLists:
    """The bug that produced a whole meeting of empty-handed participants.

    The manifest listed paths relative to the project folder while ``read_file``
    accepts only ``/mnt/user-data/...``. Every participant reported "every file
    read was denied", each returned no result, and the chair could only record
    that it had nothing to synthesize from — a three-minute meeting that
    produced nothing, from a path prefix.
    """

    def test_manifest_paths_are_the_paths_a_worker_may_open(self, tmp_path):
        from deerflow.agents.dbtl.stage_execution import (
            WORKSPACE_VIRTUAL_ROOT,
            _project_manifest,
        )

        (tmp_path / "outputs").mkdir()
        (tmp_path / "outputs" / "design.json").write_text("{}", encoding="utf-8")

        entries = _project_manifest(str(tmp_path))

        assert entries, "the manifest listed nothing"
        for entry in entries:
            assert entry["path"].startswith(f"{WORKSPACE_VIRTUAL_ROOT}/"), entry
        assert any(entry["path"] == f"{WORKSPACE_VIRTUAL_ROOT}/outputs/design.json" for entry in entries)

    def test_the_ignored_directories_are_still_ignored(self, tmp_path):
        from deerflow.agents.dbtl.stage_execution import _project_manifest

        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("x", encoding="utf-8")
        (tmp_path / "keep.txt").write_text("x", encoding="utf-8")

        paths = [entry["path"] for entry in _project_manifest(str(tmp_path))]

        assert not any(".git" in path for path in paths)
        assert any(path.endswith("/keep.txt") for path in paths)

    def test_every_worker_prompt_states_the_prefix_its_tools_require(self):
        """A worker that constructs a path has no other way to learn it."""
        from deerflow.agents.dbtl.stage_execution import _proposed_units
        from deerflow.dbtl.agent_selector import Assignment
        from deerflow.dbtl.council_proposal import CouncilProposal, ProposedSeat
        from deerflow.dbtl.stage_runner import WORKSPACE_PATH_NOTE, build_prompt

        assert "/mnt/user-data/" in WORKSPACE_PATH_NOTE

        spec = resolve_stage_spec("design", domain_profile="generic")
        proposal = CouncilProposal(
            positions=(ProposedSeat(focus="stats", brief="Argue from statistics.", agent_name="general-purpose", capability=Capability.EXPERIMENTAL_DESIGN),),
            chair=ProposedSeat(focus="chair", brief="Weigh.", agent_name="general-purpose", capability=Capability.EXPERIMENTAL_DESIGN),
        )
        proposed = _proposed_units(proposal, spec, attempt_id="dbtl-x", context="{}")
        assert WORKSPACE_PATH_NOTE in proposed[0].prompt

        selected = build_prompt(
            spec,
            Assignment(capability=Capability.EXPERIMENTAL_DESIGN, agent_name="general-purpose", via_generalist=True),
            context="{}",
        )
        assert WORKSPACE_PATH_NOTE in selected
