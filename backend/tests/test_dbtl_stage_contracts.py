"""Phase 6: stage specs, agent selection, worker results, and the fan-out.

Four modules, one theme: a stage attempt has to be reconstructable from its own
record. That is what makes an approval mean something later — a reviewer
approved *this* contract, run by *these* workers, under *that* budget — and it
is why version pinning, deterministic selection, and rejecting unstructured
worker output are all tested as guarantees rather than conveniences.
"""

from __future__ import annotations

import json

import pytest

from deerflow.dbtl.agent_selector import (
    GENERALIST_AGENT,
    AgentCandidate,
    build_candidates,
    select_agents,
)
from deerflow.dbtl.capabilities import Capability, parse_capabilities, parse_capability
from deerflow.dbtl.cycle_state import STAGE_ORDER, CycleClass
from deerflow.dbtl.stage_runner import (
    DispatchOutcome,
    collect_results,
    plan_stage,
    run_stage,
)
from deerflow.dbtl.stage_spec import (
    BUILD_SPEC_V1,
    DESIGN_SPEC_V1,
    DESIGN_SPEC_V2,
    EXECUTABLE_STAGES,
    LEARN_SPEC_V1,
    RECONCILIATION_SPEC_V1,
    TEST_SPEC_V1,
    CycleWeight,
    MemoryWritePolicy,
    StageSpec,
    StageSpecNotFound,
    WorkerBudget,
    current_spec_keys,
    describe_specs,
    parse_cycle_weight,
    registered_spec_keys,
    resolve_spec_by_key,
    resolve_stage_spec,
    specs_for_cycle,
)
from deerflow.dbtl.worker_result import (
    CAPPED_STOP_REASONS,
    WorkerResultRejected,
    WorkerStatus,
    extract_result_payload,
    parse_worker_result,
)


def _valid_payload(**overrides) -> dict:
    payload = {
        "status": "completed",
        "summary": "Reconciled three sources.",
        "claims": ["Yield units differ between sources."],
        "evidence_refs": [{"kind": "dataset", "reference": "yield_2024", "description": "header row"}],
        "limitations": ["Only 2024 environments examined."],
        "quality_checks": [{"name": "row count matches manifest", "passed": True, "detail": ""}],
        "recommended_next_actions": ["Confirm the conversion factor."],
        "provenance": {"tools_used": ["read_file"]},
    }
    payload.update(overrides)
    return payload


class TestStageSpecRegistry:
    """A stage is versioned data, and an attempt must be able to name its version."""

    def test_all_five_stages_are_executable(self) -> None:
        assert EXECUTABLE_STAGES == (
            "design",
            "reconciliation",
            "build",
            "test",
            "learn",
        )

    def test_every_executable_stage_is_a_real_cycle_stage(self) -> None:
        assert set(EXECUTABLE_STAGES) <= set(STAGE_ORDER)

    def test_resolving_without_a_version_still_returns_a_pinned_one(self) -> None:
        spec = resolve_stage_spec("design")
        assert spec.spec_key == "generic:design:v2"
        assert spec.version == 2

    def test_a_recorded_spec_key_round_trips(self) -> None:
        # The invalidation rule depends on this: an attempt records a key and a
        # later check has to fetch back the exact contract it ran under.
        for key in registered_spec_keys():
            assert resolve_spec_by_key(key).spec_key == key

    def test_an_unregistered_key_is_refused_rather_than_defaulted(self) -> None:
        with pytest.raises(StageSpecNotFound):
            resolve_spec_by_key("generic:design:v99")

    def test_stage_and_profile_lookups_are_case_insensitive(self) -> None:
        assert resolve_stage_spec("DESIGN", domain_profile="Generic").spec_key == "generic:design:v2"

    def test_an_unknown_domain_profile_is_refused(self) -> None:
        with pytest.raises(StageSpecNotFound):
            resolve_stage_spec("design", domain_profile="maize-gs")

    def test_current_keys_cover_every_executable_stage(self) -> None:
        assert current_spec_keys() == (
            "generic:design:v2",
            "generic:reconciliation:v1",
            "generic:build:v1",
            "generic:test:v1",
            "generic:learn:v1",
        )

    def test_both_specs_apply_to_every_cycle_class_and_weight(self) -> None:
        for cycle_class in CycleClass:
            for weight in CycleWeight:
                assert len(specs_for_cycle(cycle_class, weight)) == 5

    def test_no_spec_permits_agent_approval(self) -> None:
        # Human reviewers approve all gates initially; the design allows
        # revisiting that only through a separately reviewed policy change.
        for key in registered_spec_keys():
            assert resolve_spec_by_key(key).human_gate_policy.allows_agent_approval is False

    def test_design_requires_operational_criteria(self) -> None:
        assert "operational_success_criteria" in DESIGN_SPEC_V2.validity_gates
        assert DESIGN_SPEC_V2.output_schema == "design_brief.v2"

    def test_reconciliation_requires_immutable_raw_data_and_bound_hashes(self) -> None:
        assert "raw_data_immutable" in RECONCILIATION_SPEC_V1.validity_gates
        assert "dataset_hashes_bound" in RECONCILIATION_SPEC_V1.validity_gates

    def test_build_requires_reconciled_lineage_and_reproducibility(self) -> None:
        assert BUILD_SPEC_V1.required_inputs == (
            "approved_design_brief",
            "approved_reconciliation_report",
            "bound_dataset_fingerprint",
        )
        assert "reproducible_execution" in BUILD_SPEC_V1.validity_gates

    def test_learn_can_only_create_candidates(self) -> None:
        assert LEARN_SPEC_V1.output_schema == "learn_summary.v1"
        assert LEARN_SPEC_V1.memory_write_policy is MemoryWritePolicy.CANDIDATE_ONLY
        assert Capability.KNOWLEDGE_SYNTHESIS in LEARN_SPEC_V1.required_capabilities

    def test_test_uses_a_versioned_validity_pack(self) -> None:
        assert "generic-predictive:v1" in TEST_SPEC_V1.validity_gates

    def test_a_spec_without_required_capabilities_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no required capabilities"):
            StageSpec(
                stage="design",
                domain_profile="generic",
                version=2,
                title="t",
                purpose="p",
                cycle_classes=tuple(CycleClass),
                cycle_weights=tuple(CycleWeight),
                required_inputs=(),
                required_artifact_types=("x",),
                output_schema="x.v1",
                required_capabilities=(),
            )

    def test_a_capability_cannot_be_both_required_and_optional(self) -> None:
        with pytest.raises(ValueError, match="both required and optional"):
            StageSpec(
                stage="design",
                domain_profile="generic",
                version=3,
                title="t",
                purpose="p",
                cycle_classes=tuple(CycleClass),
                cycle_weights=tuple(CycleWeight),
                required_inputs=(),
                required_artifact_types=("x",),
                output_schema="x.v1",
                required_capabilities=(Capability.EXPERIMENTAL_DESIGN,),
                optional_capabilities=(Capability.EXPERIMENTAL_DESIGN,),
            )

    def test_a_non_positive_budget_is_refused(self) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            WorkerBudget(max_workers=0)

    def test_the_spec_projection_is_json_safe(self) -> None:
        described = describe_specs([DESIGN_SPEC_V1, RECONCILIATION_SPEC_V1])
        json.dumps(described)
        assert described[0]["spec_key"] == "generic:design:v1"

    def test_cycle_weight_defaults_to_full(self) -> None:
        assert parse_cycle_weight(None) is CycleWeight.FULL
        assert parse_cycle_weight("") is CycleWeight.FULL
        with pytest.raises(ValueError):
            parse_cycle_weight("enormous")


class TestCapabilities:
    def test_an_unknown_capability_is_refused_not_dropped(self) -> None:
        # Silently dropping one turns "requires quantitative genetics" into
        # "requires nothing", which is the wrong way to fail.
        with pytest.raises(ValueError, match="Unknown capability"):
            parse_capability("vibes")

    def test_parsing_preserves_order_and_dedupes(self) -> None:
        parsed = parse_capabilities(["statistical_analysis", "experimental_design", "statistical_analysis"])
        assert parsed == (Capability.STATISTICAL_ANALYSIS, Capability.EXPERIMENTAL_DESIGN)

    def test_a_bare_string_is_not_a_capability_list(self) -> None:
        with pytest.raises(ValueError):
            parse_capabilities("statistical_analysis")


class TestAgentSelection:
    """Selection is constrained, deterministic, and honest about fallbacks."""

    def test_no_agents_means_an_unsatisfied_selection(self) -> None:
        result = select_agents(DESIGN_SPEC_V1, [])
        assert not result.satisfied
        assert result.unmet_capabilities == DESIGN_SPEC_V1.required_capabilities

    def test_an_uncoverable_required_capability_is_reported(self) -> None:
        candidates = [AgentCandidate(name="literature-bot", capabilities=frozenset({Capability.LITERATURE_REVIEW}))]
        result = select_agents(RECONCILIATION_SPEC_V1, candidates)
        assert not result.satisfied
        assert Capability.DATA_RECONCILIATION in result.unmet_capabilities

    def test_a_specialist_beats_a_generalist(self) -> None:
        candidates = [
            AgentCandidate(name=GENERALIST_AGENT, is_generalist=True),
            AgentCandidate(name="steward", capabilities=frozenset({Capability.DATA_RECONCILIATION})),
        ]
        result = select_agents(RECONCILIATION_SPEC_V1, candidates)
        assert result.assignments[0].agent_name == "steward"
        assert result.assignments[0].via_generalist is False

    def test_a_generalist_fallback_is_recorded_not_hidden(self) -> None:
        # A reviewer reading "data reconciliation: general-purpose" knows what
        # they are looking at; a reviewer reading nothing does not.
        result = select_agents(RECONCILIATION_SPEC_V1, [AgentCandidate(name=GENERALIST_AGENT, is_generalist=True)])
        assert result.satisfied
        assert Capability.DATA_RECONCILIATION in result.used_generalist_for
        assert any("general-purpose" in note for note in result.notes)

    def test_an_unavailable_agent_is_not_selected(self) -> None:
        candidates = [AgentCandidate(name="steward", capabilities=frozenset({Capability.DATA_RECONCILIATION}), available=False)]
        assert not select_agents(RECONCILIATION_SPEC_V1, candidates).satisfied

    def test_optional_capabilities_only_spend_budget_on_specialists(self) -> None:
        # A generalist restating the required worker's answer adds cost and no
        # independent evidence.
        result = select_agents(DESIGN_SPEC_V1, [AgentCandidate(name=GENERALIST_AGENT, is_generalist=True)])
        assert len(result.assignments) == 1

    def test_optional_specialists_are_dispatched_up_to_the_budget(self) -> None:
        candidates = [
            AgentCandidate(name="designer", capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN})),
            AgentCandidate(name="geneticist", capabilities=frozenset({Capability.QUANTITATIVE_GENETICS})),
            AgentCandidate(name="statistician", capabilities=frozenset({Capability.STATISTICAL_ANALYSIS})),
            AgentCandidate(name="librarian", capabilities=frozenset({Capability.LITERATURE_REVIEW})),
        ]
        result = select_agents(DESIGN_SPEC_V1, candidates)
        assert len(result.assignments) == DESIGN_SPEC_V1.budget.max_workers
        assert result.dropped_for_budget
        assert result.satisfied

    def test_selection_is_deterministic(self) -> None:
        # A stage attempt that cannot be reproduced from its record is not
        # evidence of anything.
        candidates = [
            AgentCandidate(name="steward-a", capabilities=frozenset({Capability.DATA_RECONCILIATION})),
            AgentCandidate(name="steward-b", capabilities=frozenset({Capability.DATA_RECONCILIATION})),
        ]
        first = select_agents(RECONCILIATION_SPEC_V1, candidates)
        second = select_agents(RECONCILIATION_SPEC_V1, candidates)
        assert first == second
        assert first.assignments[0].agent_name == "steward-a"

    def test_an_undeclared_agent_covers_nothing(self) -> None:
        # Treating an undeclared agent as capable of everything would make the
        # selection record meaningless.
        candidates = build_candidates(["mystery-agent"])
        assert not select_agents(RECONCILIATION_SPEC_V1, candidates).satisfied

    def test_the_builtin_generalist_is_built_as_a_generalist(self) -> None:
        candidates = build_candidates([GENERALIST_AGENT])
        assert candidates[0].is_generalist is True

    def test_a_declared_agent_named_general_purpose_is_not_a_generalist(self) -> None:
        candidates = build_candidates([GENERALIST_AGENT], declared_capabilities={GENERALIST_AGENT: [Capability.LITERATURE_REVIEW]})
        assert candidates[0].is_generalist is False

    def test_unavailable_names_are_marked_offline(self) -> None:
        candidates = build_candidates(["steward"], unavailable=["steward"])
        assert candidates[0].available is False

    def test_the_selection_projection_is_json_safe(self) -> None:
        json.dumps(select_agents(DESIGN_SPEC_V1, build_candidates([GENERALIST_AGENT])).as_dict())


class TestWorkerResultContract:
    """Free-form text alone cannot satisfy a stage contract."""

    def test_a_valid_payload_parses(self) -> None:
        result = parse_worker_result(_valid_payload(), capability="data_reconciliation", agent_name="steward")
        assert result.status is WorkerStatus.COMPLETED
        assert result.is_trustworthy

    def test_needs_input_requires_one_focused_clarification(self) -> None:
        result = parse_worker_result(
            _valid_payload(
                status="needs_input",
                claims=[],
                evidence_refs=[],
                clarification_question="Which harvest environments are the held-out validation set?",
            ),
            capability="design_council_chair",
            agent_name="chair",
        )
        assert result.clarification_question == ("Which harvest environments are the held-out validation set?")
        assert not result.is_trustworthy

        with pytest.raises(WorkerResultRejected, match="clarification_question"):
            parse_worker_result(
                _valid_payload(
                    status="needs_input",
                    claims=[],
                    evidence_refs=[],
                    clarification_question="",
                ),
                capability="design_council_chair",
                agent_name="chair",
            )

    def test_the_dispatcher_owns_capability_and_agent_not_the_worker(self) -> None:
        # Accepting the worker's own account of which capability it exercised
        # would let a selection failure look like a satisfied requirement.
        result = parse_worker_result(
            _valid_payload(capability="quantitative_genetics", agent_name="impostor"),
            capability="data_reconciliation",
            agent_name="steward",
        )
        assert result.capability == "data_reconciliation"
        assert result.agent_name == "steward"

    def test_prose_instead_of_json_is_rejected(self) -> None:
        with pytest.raises(WorkerResultRejected, match="prose"):
            extract_result_payload("I reconciled the sources and everything looks fine.")

    def test_json_wrapped_in_prose_or_a_fence_is_recovered(self) -> None:
        text = "Here you go:\n```json\n" + json.dumps(_valid_payload()) + "\n```\nHope that helps."
        assert extract_result_payload(text)["status"] == "completed"

    def test_malformed_json_is_rejected_rather_than_salvaged(self) -> None:
        with pytest.raises(WorkerResultRejected, match="not valid JSON"):
            extract_result_payload('{"status": "completed", "summary": }')

    def test_an_empty_response_is_rejected(self) -> None:
        with pytest.raises(WorkerResultRejected, match="no output"):
            extract_result_payload("   ")

    def test_a_missing_status_is_rejected(self) -> None:
        payload = _valid_payload()
        del payload["status"]
        with pytest.raises(WorkerResultRejected, match="status"):
            parse_worker_result(payload, capability="c", agent_name="a")

    def test_an_unknown_status_is_rejected(self) -> None:
        with pytest.raises(WorkerResultRejected, match="Unknown worker status"):
            parse_worker_result(_valid_payload(status="mostly_fine"), capability="c", agent_name="a")

    def test_an_empty_summary_is_rejected(self) -> None:
        with pytest.raises(WorkerResultRejected, match="summary"):
            parse_worker_result(_valid_payload(summary="  "), capability="c", agent_name="a")

    def test_a_claim_without_evidence_is_rejected(self) -> None:
        # A claim with nothing behind it reads as a finding and survives into a
        # reviewer's summary without anyone able to check it.
        with pytest.raises(WorkerResultRejected, match="evidence"):
            parse_worker_result(_valid_payload(evidence_refs=[]), capability="c", agent_name="a")

    def test_a_result_with_no_claims_needs_no_evidence(self) -> None:
        result = parse_worker_result(_valid_payload(claims=[], evidence_refs=[]), capability="c", agent_name="a")
        assert result.claims == ()

    def test_an_unknown_evidence_kind_is_rejected(self) -> None:
        payload = _valid_payload(evidence_refs=[{"kind": "hunch", "reference": "x"}])
        with pytest.raises(WorkerResultRejected, match="evidence kind"):
            parse_worker_result(payload, capability="c", agent_name="a")

    def test_a_non_boolean_quality_check_is_rejected_not_coerced(self) -> None:
        # A truthy string here would turn an unanswered check into a passing one.
        payload = _valid_payload(quality_checks=[{"name": "folds disjoint", "passed": "yes"}])
        with pytest.raises(WorkerResultRejected, match="boolean"):
            parse_worker_result(payload, capability="c", agent_name="a")

    def test_a_capped_run_is_not_trustworthy_even_when_completed(self) -> None:
        # SubagentExecutor reports a budget cap as a completed run carrying a
        # partial answer, so reading status alone would file a truncated
        # investigation as finished work.
        for reason in sorted(CAPPED_STOP_REASONS):
            result = parse_worker_result(_valid_payload(), capability="c", agent_name="a", stop_reason=reason)
            assert result.status is WorkerStatus.COMPLETED
            assert result.was_capped
            assert not result.is_trustworthy

    def test_capped_stop_reasons_match_the_executor_contract(self) -> None:
        from deerflow.subagents.status_contract import SUBAGENT_STOP_REASON_VALUES

        assert CAPPED_STOP_REASONS == set(SUBAGENT_STOP_REASON_VALUES)

    def test_a_blocked_result_is_not_trustworthy_output(self) -> None:
        result = parse_worker_result(_valid_payload(status="blocked"), capability="c", agent_name="a")
        assert not result.is_trustworthy

    def test_the_result_projection_is_json_safe(self) -> None:
        json.dumps(parse_worker_result(_valid_payload(), capability="c", agent_name="a").as_dict())


class TestStageFanOut:
    """Planning, dispatching, and folding partial failures back together."""

    def _candidates(self) -> list[AgentCandidate]:
        return [AgentCandidate(name="steward", capabilities=frozenset({Capability.DATA_RECONCILIATION}))]

    def test_an_unsatisfiable_plan_is_not_dispatchable(self) -> None:
        plan = plan_stage(RECONCILIATION_SPEC_V1, [], attempt_id="a1")
        assert not plan.dispatchable

    def test_an_unsatisfiable_plan_is_never_dispatched(self) -> None:
        # Running the workers that could be matched while a required capability
        # went uncovered produces partial evidence that looks complete.
        calls: list[object] = []

        def dispatcher(units, *, budget):
            calls.append(units)
            return []

        outcome = run_stage(RECONCILIATION_SPEC_V1, [], dispatcher, attempt_id="a1")
        assert calls == []
        assert outcome.results == ()
        assert not outcome.produced_usable_evidence

    def test_a_stage_run_never_satisfies_a_gate(self) -> None:
        # Carried over from the Phase 5 stub: a gate is closed by a typed human
        # review, and a graph node is not a reviewer.
        def dispatcher(units, *, budget):
            return [DispatchOutcome(unit_id=unit.unit_id, text=json.dumps(_valid_payload())) for unit in units]

        outcome = run_stage(RECONCILIATION_SPEC_V1, self._candidates(), dispatcher, attempt_id="a1")
        assert outcome.satisfies_gate is False
        assert outcome.produced_usable_evidence

    def test_the_complete_worker_budget_comes_from_the_spec(self) -> None:
        seen: list[WorkerBudget] = []

        def dispatcher(units, *, budget):
            seen.append(budget)
            return [DispatchOutcome(unit_id=unit.unit_id, text=json.dumps(_valid_payload())) for unit in units]

        run_stage(RECONCILIATION_SPEC_V1, self._candidates(), dispatcher, attempt_id="a1")
        assert seen == [RECONCILIATION_SPEC_V1.budget]
        assert seen[0].max_turns == RECONCILIATION_SPEC_V1.budget.max_turns
        assert seen[0].max_tokens == RECONCILIATION_SPEC_V1.budget.max_tokens
        assert seen[0].timeout_seconds == RECONCILIATION_SPEC_V1.budget.timeout_seconds

    def test_a_worker_that_never_reported_becomes_a_failed_result(self) -> None:
        plan = plan_stage(RECONCILIATION_SPEC_V1, self._candidates(), attempt_id="a1")
        outcome = collect_results(plan, [])
        assert len(outcome.results) == len(plan.units)
        assert outcome.results[0].status is WorkerStatus.FAILED
        assert not outcome.produced_usable_evidence

    def test_a_crashed_worker_is_kept_not_dropped(self) -> None:
        # A fan-out where a worker crashed must not read as a tidy smaller run.
        plan = plan_stage(RECONCILIATION_SPEC_V1, self._candidates(), attempt_id="a1")
        outcome = collect_results(plan, [DispatchOutcome(unit_id=plan.units[0].unit_id, text=None, error="sandbox unavailable")])
        assert outcome.results[0].status is WorkerStatus.FAILED
        assert "sandbox unavailable" in outcome.results[0].summary

    def test_unstructured_output_becomes_a_failed_result_and_is_recorded(self) -> None:
        plan = plan_stage(RECONCILIATION_SPEC_V1, self._candidates(), attempt_id="a1")
        outcome = collect_results(plan, [DispatchOutcome(unit_id=plan.units[0].unit_id, text="all good!")])
        assert outcome.results[0].status is WorkerStatus.FAILED
        assert outcome.rejected and plan.units[0].unit_id in outcome.rejected[0]

    def test_every_unit_produces_exactly_one_result(self) -> None:
        candidates = self._candidates() + [
            AgentCandidate(name="qc", capabilities=frozenset({Capability.FIELD_TRIAL_QC})),
            AgentCandidate(name="stats", capabilities=frozenset({Capability.STATISTICAL_ANALYSIS})),
        ]
        plan = plan_stage(RECONCILIATION_SPEC_V1, candidates, attempt_id="a1")
        outcome = collect_results(plan, [DispatchOutcome(unit_id=plan.units[0].unit_id, text=json.dumps(_valid_payload()))])
        assert len(outcome.results) == len(plan.units) > 1
        assert len(outcome.trustworthy_results) == 1

    def test_the_prompt_states_the_raw_data_and_approval_constraints(self) -> None:
        plan = plan_stage(RECONCILIATION_SPEC_V1, self._candidates(), attempt_id="a1", context="Project G2F")
        prompt = plan.units[0].prompt
        assert "raw data" in prompt
        assert "cannot approve this stage" in prompt
        assert "do not pick a winner" in prompt
        assert "Project G2F" in prompt

    def test_unit_ids_are_stable_for_one_attempt(self) -> None:
        first = plan_stage(RECONCILIATION_SPEC_V1, self._candidates(), attempt_id="a1")
        second = plan_stage(RECONCILIATION_SPEC_V1, self._candidates(), attempt_id="a1")
        assert [unit.unit_id for unit in first.units] == [unit.unit_id for unit in second.units]

    def test_the_outcome_projection_is_json_safe(self) -> None:
        plan = plan_stage(RECONCILIATION_SPEC_V1, self._candidates(), attempt_id="a1")
        json.dumps(collect_results(plan, []).as_dict())
