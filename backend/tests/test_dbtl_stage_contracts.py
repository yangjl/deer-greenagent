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
)
from deerflow.dbtl.stage_spec import (
    BUILD_SPEC_V12,
    DESIGN_SPEC_V1,
    DESIGN_SPEC_V2,
    EXECUTABLE_STAGES,
    LEARN_SPEC_V1,
    RECONCILIATION_SPEC_V1,
    TEST_SPEC_V1,
    TEST_SPEC_V2,
    TEST_SPEC_V3,
    TEST_SPEC_V4,
    CycleWeight,
    MemoryWritePolicy,
    StageSpec,
    StageSpecNotFound,
    WorkerBudget,
    describe_specs,
    resolve_spec_by_key,
    resolve_stage_spec,
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


ALL_SPECS = (
    DESIGN_SPEC_V1,
    DESIGN_SPEC_V2,
    RECONCILIATION_SPEC_V1,
    BUILD_SPEC_V12,
    TEST_SPEC_V1,
    TEST_SPEC_V2,
    TEST_SPEC_V3,
    TEST_SPEC_V4,
    LEARN_SPEC_V1,
)


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
        for spec in ALL_SPECS:
            assert resolve_spec_by_key(spec.spec_key) is spec

    def test_an_unregistered_key_is_refused_rather_than_defaulted(self) -> None:
        with pytest.raises(StageSpecNotFound):
            resolve_spec_by_key("generic:design:v99")

    def test_stage_and_profile_lookups_are_case_insensitive(self) -> None:
        assert resolve_stage_spec("DESIGN", domain_profile="Generic").spec_key == "generic:design:v2"

    def test_an_unknown_domain_profile_is_refused(self) -> None:
        with pytest.raises(StageSpecNotFound):
            resolve_stage_spec("design", domain_profile="maize-gs")

    def test_no_spec_permits_agent_approval(self) -> None:
        # Human reviewers approve all gates initially; the design allows
        # revisiting that only through a separately reviewed policy change.
        for spec in ALL_SPECS:
            assert spec.human_gate_policy.allows_agent_approval is False

    def test_design_requires_operational_criteria(self) -> None:
        assert "operational_success_criteria" in DESIGN_SPEC_V2.validity_gates
        assert DESIGN_SPEC_V2.output_schema == "design_brief.v2"

    def test_reconciliation_requires_immutable_raw_data_and_bound_hashes(self) -> None:
        assert "raw_data_immutable" in RECONCILIATION_SPEC_V1.validity_gates
        assert "dataset_hashes_bound" in RECONCILIATION_SPEC_V1.validity_gates

    def test_current_build_discovers_and_server_binds_its_inputs(self) -> None:
        assert BUILD_SPEC_V12.required_inputs == (
            "approved_design_brief",
            "workspace_inputs_examined_during_build",
        )
        assert "server_bound_input_lineage" in BUILD_SPEC_V12.validity_gates
        assert BUILD_SPEC_V12.output_schema == "build_package.v12"

    def test_current_build_has_enough_bounded_turns_to_execute(self) -> None:
        assert BUILD_SPEC_V12.budget.max_turns == 450
        assert BUILD_SPEC_V12.budget.max_tokens == 120_000
        assert BUILD_SPEC_V12.budget.token_limit_enforced is True

    def test_learn_can_only_create_candidates(self) -> None:
        assert LEARN_SPEC_V1.output_schema == "learn_summary.v1"
        assert LEARN_SPEC_V1.memory_write_policy is MemoryWritePolicy.CANDIDATE_ONLY
        assert Capability.KNOWLEDGE_SYNTHESIS in LEARN_SPEC_V1.required_capabilities

    def test_test_uses_a_versioned_validity_pack(self) -> None:
        assert "generic-predictive:v1" in TEST_SPEC_V1.validity_gates
        assert TEST_SPEC_V2.budget.max_turns == 143
        assert TEST_SPEC_V3.validity_gates == ("generic-predictive:v2",)
        assert TEST_SPEC_V3.budget == TEST_SPEC_V2.budget
        assert TEST_SPEC_V4.validity_gates == (
            "generic-predictive:v2",
            "server_verified_build_rerun",
        )
        assert TEST_SPEC_V4.budget == TEST_SPEC_V3.budget

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

    def test_build_normalizes_descriptive_file_kinds_to_workspace_files(self) -> None:
        result = parse_worker_result(
            _valid_payload(
                evidence_refs=[
                    {
                        "kind": "manifest",
                        "reference": "/mnt/user-data/outputs/.dbtl-stage-work/run/artifacts/run_manifest.json",
                        "description": "Checksummed output manifest.",
                    },
                    {
                        "kind": "execution_log",
                        "reference": "/mnt/user-data/outputs/.dbtl-stage-work/run/logs/run.log",
                        "description": "Execution log.",
                    },
                    {
                        "kind": "implementation",
                        "reference": "/mnt/user-data/outputs/.dbtl-stage-work/run/src/simulate.py",
                        "description": "Simulator implementation.",
                    },
                ]
            ),
            capability="software_engineering",
            agent_name="general-purpose",
            stage="build",
        )

        assert [item.kind for item in result.evidence_refs] == ["workspace_file"] * 3

    def test_build_accepts_redundant_identical_evidence_locators(self) -> None:
        path = "/mnt/user-data/outputs/.dbtl-stage-work/run/logs/run.log"
        result = parse_worker_result(
            _valid_payload(
                evidence_refs=[
                    {
                        "kind": "execution_log",
                        "reference": path,
                        "path": path,
                        "description": "Execution log.",
                    }
                ]
            ),
            capability="software_engineering",
            agent_name="general-purpose",
            stage="build",
        )

        assert result.evidence_refs[0].kind == "workspace_file"
        assert result.evidence_refs[0].reference == path

    def test_descriptive_file_kinds_remain_strict_outside_build(self) -> None:
        payload = _valid_payload(
            evidence_refs=[
                {
                    "kind": "manifest",
                    "reference": "/mnt/user-data/outputs/run_manifest.json",
                }
            ]
        )

        with pytest.raises(WorkerResultRejected, match="Unknown evidence kind 'manifest'"):
            parse_worker_result(payload, capability="data_reconciliation", agent_name="steward")

    def test_build_does_not_coerce_an_unknown_non_file_reference(self) -> None:
        payload = _valid_payload(
            evidence_refs=[
                {
                    "kind": "manifest",
                    "reference": "a-logical-id-that-is-not-a-workspace-file",
                }
            ]
        )

        with pytest.raises(WorkerResultRejected, match="Unknown evidence kind 'manifest'"):
            parse_worker_result(
                payload,
                capability="software_engineering",
                agent_name="general-purpose",
                stage="build",
            )

    def test_build_normalizes_a_named_boolean_quality_check_map(self) -> None:
        result = parse_worker_result(
            _valid_payload(
                quality_checks={
                    "implementation_written": True,
                    "complete_simulation_executed": False,
                }
            ),
            capability="software_engineering",
            agent_name="general-purpose",
            stage="build",
        )

        assert [(item.name, item.passed) for item in result.quality_checks] == [
            ("implementation_written", True),
            ("complete_simulation_executed", False),
        ]

    def test_exact_boolean_strings_are_losslessly_normalized(self) -> None:
        result = parse_worker_result(
            _valid_payload(
                quality_checks={
                    "implementation_written": "true",
                    "complete_simulation_executed": "false",
                }
            ),
            capability="software_engineering",
            agent_name="general-purpose",
            stage="build",
        )

        assert [(item.name, item.passed) for item in result.quality_checks] == [
            ("implementation_written", True),
            ("complete_simulation_executed", False),
        ]

    def test_a_quality_check_map_remains_strict_outside_build(self) -> None:
        with pytest.raises(WorkerResultRejected, match="list of objects"):
            parse_worker_result(
                _valid_payload(quality_checks={"implementation_written": True}),
                capability="data_reconciliation",
                agent_name="steward",
            )

    @pytest.mark.parametrize("value", ["passed", 1, None])
    def test_build_does_not_coerce_untyped_quality_check_verdicts(self, value: object) -> None:
        with pytest.raises(WorkerResultRejected, match="must be a boolean or an object"):
            parse_worker_result(
                _valid_payload(quality_checks={"implementation_written": value}),
                capability="software_engineering",
                agent_name="general-purpose",
                stage="build",
            )

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

    def test_an_unresolved_completed_design_chair_is_normalized_to_needs_input(self) -> None:
        result = parse_worker_result(
            _valid_payload(
                status="completed",
                consensus={
                    "version": 1,
                    "agreements": ["Use an additive pilot."],
                    "disagreements": [],
                    "open_questions": ["Which heritability should the pilot use?"],
                },
            ),
            capability="design_council_chair",
            agent_name="chair",
        )

        assert result.status is WorkerStatus.NEEDS_INPUT
        assert result.clarification_question == "Which heritability should the pilot use?"
        assert not result.is_trustworthy

    def test_unresolved_consensus_does_not_pause_a_non_chair_worker(self) -> None:
        result = parse_worker_result(
            _valid_payload(
                status="completed",
                consensus={
                    "version": 1,
                    "agreements": ["Use an additive pilot."],
                    "disagreements": [],
                    "open_questions": ["Which heritability should the pilot use?"],
                },
            ),
            capability="quantitative_genetics",
            agent_name="position",
        )

        assert result.status is WorkerStatus.COMPLETED

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

    def test_build_may_infer_missing_status_only_from_the_required_completion_check(self) -> None:
        payload = _valid_payload(
            quality_checks=[
                {"name": "phase_done_condition", "passed": True, "detail": "Every expected output exists."},
            ]
        )
        del payload["status"]

        result = parse_worker_result(payload, capability="software_engineering", agent_name="general-purpose", stage="build")

        assert result.status is WorkerStatus.COMPLETED

    def test_build_incomplete_status_is_a_truthful_failure(self) -> None:
        result = parse_worker_result(
            _valid_payload(status="incomplete"),
            capability="software_engineering",
            agent_name="general-purpose",
            stage="build",
        )

        assert result.status is WorkerStatus.FAILED
        assert not result.is_trustworthy

    def test_build_recovers_nested_failed_phase_envelope(self) -> None:
        result = parse_worker_result(
            {
                "phase": {
                    "status": "incomplete",
                    "decision_reason": "Validation stopped before its required figures were generated.",
                },
                "artifact_refs": {
                    "created": {
                        "validator": "/mnt/user-data/outputs/.dbtl-stage-work/run/src/validate.py",
                        "log": "/mnt/user-data/outputs/.dbtl-stage-work/run/logs/validation.log",
                    },
                    "required_but_not_completed": {"report": "artifacts/report.md"},
                },
                "claims": [
                    {
                        "claim": "The validator stopped before completion.",
                        "evidence": ["/mnt/user-data/outputs/.dbtl-stage-work/run/logs/validation.log"],
                    }
                ],
                "limitations": [{"item": "No PASS decision was issued."}],
                "quality_checks": [{"name": "figures", "status": "not_completed"}],
            },
            capability="field_trial_quality_control",
            agent_name="general-purpose",
            stage="build",
        )

        assert result.status is WorkerStatus.FAILED
        assert result.summary == "Validation stopped before its required figures were generated."
        assert result.artifact_refs == (
            "/mnt/user-data/outputs/.dbtl-stage-work/run/src/validate.py",
            "/mnt/user-data/outputs/.dbtl-stage-work/run/logs/validation.log",
        )
        assert result.evidence_refs[0].reference.endswith("logs/validation.log")
        assert result.limitations == ("No PASS decision was issued.",)
        assert result.quality_checks[0].passed is False

    def test_failed_build_diagnostic_mapping_does_not_become_evidence(self) -> None:
        result = parse_worker_result(
            _valid_payload(
                status="failed",
                claims=[{"claim": "No pass was asserted.", "evidence": ["Validator exit code 1"]}],
                evidence_refs={"execution": {"exit_code": 1}},
            ),
            capability="field_trial_quality_control",
            agent_name="general-purpose",
            stage="build",
        )

        assert result.status is WorkerStatus.FAILED
        assert result.claims == ()
        assert result.evidence_refs == ()

    def test_build_field_aliases_keep_the_reported_values(self) -> None:
        payload = _valid_payload()
        payload.pop("summary")
        payload.pop("artifact_refs", None)
        payload.pop("evidence_refs")
        payload.pop("claims")
        payload.pop("quality_checks")
        payload.update(
            {
                "headline": "Built and checked the pilot.",
                "outputs": [{"path": "/mnt/user-data/outputs/pilot.json"}],
                "evidence": ["/mnt/user-data/outputs/pilot.json"],
                "findings": ["The pilot output was generated."],
                "checks": {"phase_done_condition": "true"},
            }
        )

        result = parse_worker_result(payload, capability="software_engineering", agent_name="general-purpose", stage="build")

        assert result.summary == "Built and checked the pilot."
        assert result.artifact_refs == ("/mnt/user-data/outputs/pilot.json",)
        assert result.evidence_refs[0].reference == "/mnt/user-data/outputs/pilot.json"
        assert result.claims == ("The pilot output was generated.",)

    def test_an_unknown_status_is_rejected(self) -> None:
        with pytest.raises(WorkerResultRejected, match="Unknown worker status"):
            parse_worker_result(_valid_payload(status="mostly_fine"), capability="c", agent_name="a")

    @pytest.mark.parametrize("reported", ["complete", "done", "ok", "passed", "success", "succeeded"])
    def test_unambiguous_completed_status_aliases_are_normalized(self, reported: str) -> None:
        result = parse_worker_result(_valid_payload(status=reported), capability="c", agent_name="a")

        assert result.status is WorkerStatus.COMPLETED

    def test_an_empty_summary_is_rejected(self) -> None:
        with pytest.raises(WorkerResultRejected, match="summary"):
            parse_worker_result(_valid_payload(summary="  "), capability="c", agent_name="a")

    def test_a_claim_without_evidence_is_rejected(self) -> None:
        # A claim with nothing behind it reads as a finding and survives into a
        # reviewer's summary without anyone able to check it.
        with pytest.raises(WorkerResultRejected, match="evidence"):
            parse_worker_result(_valid_payload(evidence_refs=[]), capability="c", agent_name="a")

    def test_named_structured_claims_are_normalized_without_losing_evidence(self) -> None:
        """Models commonly make the claim/evidence relationship explicit.

        That is stricter than the requested string list, not an unusable
        scientific result, so the parser keeps only recognized textual fields
        rather than stringifying arbitrary objects.
        """
        result = parse_worker_result(
            _valid_payload(
                claims=[
                    {"claim": "Population structure must be preserved."},
                    {"statement": "Validation families must remain held out."},
                    {"text": "The null simulation needs zero genetic effects."},
                ]
            ),
            capability="quantitative_genetics",
            agent_name="general-purpose",
        )

        assert result.claims == (
            "Population structure must be preserved.",
            "Validation families must remain held out.",
            "The null simulation needs zero genetic effects.",
        )
        assert result.evidence_refs[0].reference == "yield_2024"

    def test_a_structured_claim_can_supply_its_own_typed_evidence(self) -> None:
        result = parse_worker_result(
            _valid_payload(
                claims=[
                    {
                        "claim": "The breeding population has a fixed scope.",
                        "evidence_refs": [
                            {
                                "kind": "workspace_file",
                                "reference": "/mnt/user-data/design.md",
                                "description": "Population definition",
                            }
                        ],
                    }
                ],
                evidence_refs=[],
            ),
            capability="quantitative_genetics",
            agent_name="general-purpose",
        )

        assert result.claims == ("The breeding population has a fixed scope.",)
        assert result.evidence_refs[0].kind == "workspace_file"
        assert result.evidence_refs[0].reference == "/mnt/user-data/design.md"

    def test_explicit_artifact_and_evidence_aliases_normalize_to_the_canonical_contract(self) -> None:
        """Do not discard completed work over a richer, lossless JSON shape."""
        result = parse_worker_result(
            _valid_payload(
                artifact_refs=[
                    {
                        "id": "artifact_spec",
                        "path": "/mnt/user-data/outputs/spec.json",
                        "role": "Normative specification",
                    }
                ],
                claims=[
                    {
                        "id": "claim_specified",
                        "statement": "The baseline is explicitly versioned.",
                        "evidence_refs": ["evidence_spec"],
                    }
                ],
                evidence_refs=[
                    {
                        "id": "evidence_spec",
                        "artifact_ref": "artifact_spec",
                        "description": "Versioned specification",
                    },
                    {
                        "id": "evidence_input",
                        "path": "/mnt/user-data/outputs/design.md",
                        "description": "Approved design input",
                    },
                ],
                quality_checks=[
                    {"name": "artifact creation", "status": "passed", "detail": "Written"},
                    {"name": "post-fix rerun", "status": "not_run", "detail": "Deadline"},
                ],
            ),
            capability="quantitative_genetics",
            agent_name="general-purpose",
        )

        assert result.artifact_refs == ("/mnt/user-data/outputs/spec.json",)
        assert [item.as_dict() for item in result.evidence_refs] == [
            {
                "kind": "workspace_file",
                "reference": "/mnt/user-data/outputs/spec.json",
                "description": "Versioned specification",
            },
            {
                "kind": "workspace_file",
                "reference": "/mnt/user-data/outputs/design.md",
                "description": "Approved design input",
            },
        ]
        assert [item.passed for item in result.quality_checks] == [True, False]

    def test_named_artifact_objects_accept_the_models_common_name_path_shape(self) -> None:
        result = parse_worker_result(
            _valid_payload(
                artifact_refs=[
                    {
                        "name": "validation_report",
                        "path": "/mnt/user-data/outputs/validation-report.md",
                    }
                ],
                evidence_refs=[
                    {
                        "artifact_ref": "validation_report",
                        "description": "Independent validation report",
                    }
                ],
            ),
            capability="validity_assessment",
            agent_name="general-purpose",
            stage="build",
        )

        assert result.artifact_refs == ("/mnt/user-data/outputs/validation-report.md",)
        assert result.evidence_refs[0].reference == "/mnt/user-data/outputs/validation-report.md"
        assert result.evidence_refs[0].kind == "workspace_file"

    def test_path_only_artifact_objects_are_accepted_without_inventing_an_alias(self) -> None:
        result = parse_worker_result(
            _valid_payload(artifact_refs=[{"path": "/mnt/user-data/outputs/validation-report.md"}]),
            capability="validity_assessment",
            agent_name="general-purpose",
            stage="build",
        )

        assert result.artifact_refs == ("/mnt/user-data/outputs/validation-report.md",)

    def test_design_chair_preserves_a_result_with_a_workspace_reference_shaped_artifact(self) -> None:
        payload = _valid_payload(
            summary="The chair preserved its synthesis.",
            artifact_refs=[
                {
                    "kind": "workspace_file",
                    "reference": "/mnt/user-data/DATA_NOTES.md",
                }
            ],
        )

        result = parse_worker_result(
            payload,
            capability="design_council_chair",
            agent_name="general-purpose",
            stage="design",
        )

        assert result.summary == "The chair preserved its synthesis."
        assert result.artifact_refs == ("/mnt/user-data/DATA_NOTES.md",)

    @pytest.mark.parametrize(
        ("capability", "stage", "reference"),
        [
            ("design_red_team", "design", "/mnt/user-data/DATA_NOTES.md"),
            ("design_council_chair", "build", "/mnt/user-data/DATA_NOTES.md"),
            ("design_council_chair", "design", "dataset-1"),
        ],
    )
    def test_workspace_reference_artifact_normalization_stays_narrow(
        self,
        capability: str,
        stage: str,
        reference: str,
    ) -> None:
        with pytest.raises(WorkerResultRejected, match="non-empty string 'path'"):
            parse_worker_result(
                _valid_payload(
                    artifact_refs=[
                        {
                            "kind": "workspace_file",
                            "reference": reference,
                        }
                    ]
                ),
                capability=capability,
                agent_name="general-purpose",
                stage=stage,
            )

    def test_ambiguous_artifact_objects_and_quality_statuses_are_still_rejected(self) -> None:
        for alias_field in ("id", "name"):
            with pytest.raises(WorkerResultRejected, match=f"'{alias_field}'.*non-empty string"):
                parse_worker_result(
                    _valid_payload(
                        artifact_refs=[
                            {
                                alias_field: "  ",
                                "path": "/mnt/user-data/outputs/spec.json",
                            }
                        ]
                    ),
                    capability="c",
                    agent_name="a",
                )
        with pytest.raises(WorkerResultRejected, match="conflicting 'id' and 'name'"):
            parse_worker_result(
                _valid_payload(
                    artifact_refs=[
                        {
                            "id": "specification",
                            "name": "validation_report",
                            "path": "/mnt/user-data/outputs/spec.json",
                        }
                    ]
                ),
                capability="c",
                agent_name="a",
            )
        with pytest.raises(WorkerResultRejected, match="unknown 'status'"):
            parse_worker_result(
                _valid_payload(quality_checks=[{"name": "unclear", "status": "mostly"}]),
                capability="c",
                agent_name="a",
            )
        with pytest.raises(WorkerResultRejected, match="conflicting"):
            parse_worker_result(
                _valid_payload(quality_checks=[{"name": "contradiction", "passed": True, "status": "failed"}]),
                capability="c",
                agent_name="a",
            )

    def test_named_contract_aliases_must_be_unique_unambiguous_and_resolved(self) -> None:
        artifact = {"id": "spec", "path": "/mnt/user-data/outputs/spec.json"}
        with pytest.raises(WorkerResultRejected, match="Duplicate artifact"):
            parse_worker_result(
                _valid_payload(artifact_refs=[artifact, artifact]),
                capability="c",
                agent_name="a",
            )
        with pytest.raises(WorkerResultRejected, match="Duplicate artifact"):
            parse_worker_result(
                _valid_payload(
                    artifact_refs=[
                        artifact,
                        {"name": "spec", "path": "/mnt/user-data/outputs/validation.json"},
                    ]
                ),
                capability="c",
                agent_name="a",
            )
        with pytest.raises(WorkerResultRejected, match="exactly one locator"):
            parse_worker_result(
                _valid_payload(
                    evidence_refs=[
                        {
                            "id": "evidence-1",
                            "path": "/mnt/user-data/outputs/spec.json",
                            "artifact_ref": "spec",
                        }
                    ]
                ),
                capability="c",
                agent_name="a",
            )
        with pytest.raises(WorkerResultRejected, match="conflicts"):
            parse_worker_result(
                _valid_payload(
                    evidence_refs=[
                        {
                            "id": "evidence-1",
                            "kind": "external",
                            "path": "/mnt/user-data/outputs/spec.json",
                        }
                    ]
                ),
                capability="c",
                agent_name="a",
            )
        with pytest.raises(WorkerResultRejected, match="unknown evidence id"):
            parse_worker_result(
                _valid_payload(
                    claims=[{"statement": "Claim", "evidence_refs": ["missing"]}],
                ),
                capability="c",
                agent_name="a",
            )
        with pytest.raises(WorkerResultRejected, match="Duplicate evidence"):
            parse_worker_result(
                _valid_payload(
                    evidence_refs=[
                        {"id": "same", "kind": "dataset", "reference": "a"},
                        {"id": "same", "kind": "dataset", "reference": "b"},
                    ]
                ),
                capability="c",
                agent_name="a",
            )

    def test_an_unnamed_claim_object_is_still_rejected(self) -> None:
        with pytest.raises(WorkerResultRejected, match="recognized text field"):
            parse_worker_result(
                _valid_payload(claims=[{"confidence": 0.9}]),
                capability="c",
                agent_name="a",
            )

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
        assert "did not satisfy the stage contract" in outcome.results[0].summary
        assert outcome.rejected and plan.units[0].unit_id in outcome.rejected[0]

    def test_a_capped_worker_is_recorded_against_its_cap_rather_than_its_output_shape(self) -> None:
        """The durable record must name the same cause the live lane shows.

        A worker stopped at its budget leaves a fragment; recording that as a
        contract violation blames the worker's formatting for a guardrail, and
        the two paths must not describe one failure two ways.
        """
        plan = plan_stage(RECONCILIATION_SPEC_V1, self._candidates(), attempt_id="a1")
        outcome = collect_results(
            plan,
            [
                DispatchOutcome(
                    unit_id=plan.units[0].unit_id,
                    text="I was still writing the result when",
                    stop_reason="token_capped",
                )
            ],
        )

        assert outcome.results[0].status is WorkerStatus.FAILED
        assert "token budget" in outcome.results[0].summary
        assert "structured result" in outcome.results[0].summary
        assert "did not satisfy the stage contract" not in outcome.results[0].summary
        # Still recorded as a rejection: the attempt produced no usable evidence.
        assert outcome.rejected and plan.units[0].unit_id in outcome.rejected[0]

    def test_build_collection_accepts_descriptive_kinds_for_workspace_files(self) -> None:
        spec = resolve_stage_spec("build")
        plan = plan_stage(spec, build_candidates([GENERALIST_AGENT]), attempt_id="a1")
        payload = _valid_payload(
            evidence_refs=[
                {
                    "kind": "test_suite",
                    "reference": "/mnt/user-data/outputs/.dbtl-stage-work/run/tests/test_build.py",
                }
            ],
            quality_checks={"implementation_written": True},
        )

        outcome = collect_results(
            plan,
            [DispatchOutcome(unit_id=plan.units[0].unit_id, text=json.dumps(payload))],
        )

        assert outcome.results[0].status is WorkerStatus.COMPLETED
        assert outcome.results[0].evidence_refs[0].kind == "workspace_file"
        assert outcome.results[0].quality_checks[0].name == "implementation_written"
        assert not outcome.rejected

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

    def test_the_generic_collector_refuses_a_unit_owned_by_another_parser(self) -> None:
        from dataclasses import replace

        from deerflow.dbtl.stage_runner import BUILD_SUMMARY_OUTPUT

        plan = plan_stage(RECONCILIATION_SPEC_V1, self._candidates(), attempt_id="a1")
        typed = replace(plan, units=(replace(plan.units[0], output_contract=BUILD_SUMMARY_OUTPUT),))

        with pytest.raises(ValueError, match="typed output must be handled by its owning parser"):
            collect_results(typed, [])


class TestBuildContract:
    """Reproducibility is Test's question and a human's verdict, not Build's gate.

    A Build worker that could not demonstrate a second identical run marked its
    own result failed, so a complete implementation produced no reviewable
    evidence at all. Build's job is to record what someone else needs in order
    to re-run it; whether the work *is* reproducible is decided against the
    Test validity pack, where ``reproducibility`` remains a required check.
    """

    def test_build_v12_is_current_and_bounded(self) -> None:
        assert resolve_stage_spec("build").spec_key == "generic:build:v12"
        assert BUILD_SPEC_V12.validity_gates == (
            "server_bound_input_lineage",
            "versioned_derived_outputs",
            "structured_rerun_spec",
            "server_verified_phase_manifest",
            "phase_declared_skills",
            "narrow_implementation_inputs",
            "granted_paths_only",
            "server_executed_entry_point",
        )
        assert BUILD_SPEC_V12.budget.max_tokens == 120_000

    def test_test_still_requires_the_reproducibility_check(self) -> None:
        from deerflow.dbtl.validity import DEFAULT_VALIDITY_PACK, ValidityCheckName

        assert ValidityCheckName.REPRODUCIBILITY in DEFAULT_VALIDITY_PACK.required_checks

    def test_the_build_worker_is_told_not_to_fail_over_an_unrepeated_run(self) -> None:
        from deerflow.dbtl.agent_selector import Assignment
        from deerflow.dbtl.stage_runner import build_prompt

        spec = resolve_stage_spec("build")
        assignment = Assignment(
            capability=Capability.SOFTWARE_ENGINEERING,
            agent_name="general-purpose",
            via_generalist=True,
        )
        prompt = build_prompt(spec, assignment, context="ctx")

        assert "Reproducibility" in prompt
        assert "NOT required" in prompt
        assert "Do not mark your own result failed" in prompt
        assert "limitations" in prompt

    def test_the_build_worker_is_told_to_classify_implementation_files_by_location(self) -> None:
        from deerflow.dbtl.agent_selector import Assignment
        from deerflow.dbtl.stage_runner import build_prompt

        spec = resolve_stage_spec("build")
        assignment = Assignment(
            capability=Capability.SOFTWARE_ENGINEERING,
            agent_name="general-purpose",
            via_generalist=True,
        )

        prompt = build_prompt(spec, assignment, context="ctx")

        assert "Evidence kind says where the evidence lives" in prompt
        assert "Use workspace_file for manifests, logs, source code, tests" in prompt

    def test_build_and_test_are_told_to_account_for_every_design_deliverable(self) -> None:
        from deerflow.dbtl.agent_selector import Assignment
        from deerflow.dbtl.stage_runner import build_prompt

        assignment = Assignment(
            capability=Capability.SOFTWARE_ENGINEERING,
            agent_name="general-purpose",
            via_generalist=True,
        )

        build = build_prompt(resolve_stage_spec("build"), assignment, context="ctx")
        test = build_prompt(resolve_stage_spec("test"), assignment, context="ctx")

        assert "provenance.deliverable_fulfillment" in build
        assert "one item for every id" in build
        assert "provenance.deliverable_audit" in test
        assert "Independently inspect every expected path" in test
