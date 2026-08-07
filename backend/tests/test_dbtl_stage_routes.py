"""The stage graph's legal edges, and the Phase 7 golden mapping.

The route-legality matrix here is also the human-readable route catalog the
manual scenario walkthroughs reference (progressive-gate plan §4): for each
state it records the inputs, the allowed route slugs, and the refusal for
everything else, so the expected menu cannot drift into undocumented policy.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.stage_routes import (
    ABANDONED,
    COMPLETED,
    GRAPH_STAGES,
    RouteContext,
    RouteSlug,
    StageRoutesRefused,
    compute_stage_routes,
    transition_target,
)
from deerflow.dbtl.validity import ValidityOutcome, WorkflowRecommendation


def _slugs(routes) -> list[str]:
    return [route.slug for route in routes]


class TestRouteLegalityMatrix:
    """Every stage × outcome."""

    def test_design_approved_offers_build_revise_close(self) -> None:
        routes = compute_stage_routes(RouteContext("design", "approve"))
        assert _slugs(routes) == [
            RouteSlug.ADVANCE,
            RouteSlug.REVISE_HERE,
            RouteSlug.PARK,
            RouteSlug.CLOSE_CYCLE,
        ]
        advance = routes[0]
        assert advance.to_stage == "build"

    def test_build_approved_offers_test(self) -> None:
        routes = compute_stage_routes(RouteContext("build", "approve"))
        assert _slugs(routes) == [
            RouteSlug.ADVANCE,
            RouteSlug.REVISE_HERE,
            RouteSlug.PARK,
            RouteSlug.CLOSE_CYCLE,
        ]
        assert routes[0].to_stage == "test"

    def test_learn_approved_concludes_only(self) -> None:
        routes = compute_stage_routes(RouteContext("learn", "approve"))
        assert _slugs(routes) == [RouteSlug.ADVANCE]
        assert routes[0].to_stage == COMPLETED

    @pytest.mark.parametrize("stage", ["design", "build", "learn"])
    def test_changes_requested_offers_revise_and_close(self, stage: str) -> None:
        routes = compute_stage_routes(RouteContext(stage, "request_changes"))
        assert _slugs(routes) == [RouteSlug.REVISE_HERE, RouteSlug.CLOSE_CYCLE]
        assert routes[0].to_stage == stage

    @pytest.mark.parametrize("stage", ["design", "build", "learn"])
    def test_rejected_offers_close_only(self, stage: str) -> None:
        routes = compute_stage_routes(RouteContext(stage, "reject"))
        assert _slugs(routes) == [RouteSlug.CLOSE_CYCLE]
        assert routes[0].to_stage == ABANDONED

    @pytest.mark.parametrize("outcome", ["supported", "not_supported"])
    def test_conclusive_test_offers_learn_repeat_close(self, outcome: str) -> None:
        routes = compute_stage_routes(RouteContext("test", outcome))
        assert _slugs(routes) == [RouteSlug.ADVANCE, RouteSlug.REVISE_HERE, RouteSlug.CLOSE_CYCLE]
        assert routes[0].to_stage == "learn"

    @pytest.mark.parametrize("outcome", ["inconclusive", "invalidated"])
    def test_unresolved_test_offers_repeat_build_design_close(self, outcome: str) -> None:
        routes = compute_stage_routes(RouteContext("test", outcome))
        expected = [RouteSlug.REVISE_HERE]
        if outcome == "invalidated":
            expected.append(RouteSlug.LEARN_FROM_INVALIDATED_EVIDENCE)
        expected.extend([RouteSlug.RETURN_TO_BUILD, RouteSlug.RETURN_TO_DESIGN, RouteSlug.CLOSE_CYCLE])
        assert _slugs(routes) == expected

    def test_no_outcome_offers_no_menu(self) -> None:
        assert compute_stage_routes(RouteContext("design", None)) == ()

    def test_unknown_stage_is_refused(self) -> None:
        with pytest.raises(StageRoutesRefused):
            compute_stage_routes(RouteContext("reconciliation", "approve"))

    def test_unknown_outcome_is_refused(self) -> None:
        with pytest.raises(StageRoutesRefused):
            compute_stage_routes(RouteContext("test", "mostly_fine"))
        with pytest.raises(StageRoutesRefused):
            compute_stage_routes(RouteContext("design", "supported"))

    def test_reconciliation_is_never_a_destination(self) -> None:
        for stage in GRAPH_STAGES:
            for outcome in ["approve", "request_changes", "reject"] if stage != "test" else list(ValidityOutcome):
                routes = compute_stage_routes(RouteContext(stage, str(outcome)))
                assert all(route.to_stage != "reconciliation" for route in routes)


class TestPhase7GoldenMapping:
    """The post-Test chooser must match the current allowed routes."""

    ALLOWED_PHASE7: dict[ValidityOutcome, set[WorkflowRecommendation]] = {
        ValidityOutcome.SUPPORTED: {WorkflowRecommendation.ADVANCE_TO_LEARN, WorkflowRecommendation.REPEAT_TEST, WorkflowRecommendation.CLOSE_CYCLE},
        ValidityOutcome.NOT_SUPPORTED: {WorkflowRecommendation.ADVANCE_TO_LEARN, WorkflowRecommendation.REPEAT_TEST, WorkflowRecommendation.CLOSE_CYCLE},
        ValidityOutcome.INCONCLUSIVE: {
            WorkflowRecommendation.REPEAT_TEST,
            WorkflowRecommendation.RETURN_TO_BUILD,
            WorkflowRecommendation.RETURN_TO_DESIGN,
            WorkflowRecommendation.CLOSE_CYCLE,
        },
        ValidityOutcome.INVALIDATED: {
            WorkflowRecommendation.LEARN_FROM_INVALIDATED_EVIDENCE,
            WorkflowRecommendation.REPEAT_TEST,
            WorkflowRecommendation.RETURN_TO_BUILD,
            WorkflowRecommendation.RETURN_TO_DESIGN,
            WorkflowRecommendation.CLOSE_CYCLE,
        },
    }

    RECOMMENDATION_TO_SLUG: dict[WorkflowRecommendation, str] = {
        WorkflowRecommendation.ADVANCE_TO_LEARN: RouteSlug.ADVANCE,
        WorkflowRecommendation.REPEAT_TEST: RouteSlug.REVISE_HERE,
        WorkflowRecommendation.RETURN_TO_BUILD: RouteSlug.RETURN_TO_BUILD,
        WorkflowRecommendation.RETURN_TO_DESIGN: RouteSlug.RETURN_TO_DESIGN,
        WorkflowRecommendation.CLOSE_CYCLE: RouteSlug.CLOSE_CYCLE,
        WorkflowRecommendation.LEARN_FROM_INVALIDATED_EVIDENCE: RouteSlug.LEARN_FROM_INVALIDATED_EVIDENCE,
    }

    @pytest.mark.parametrize("outcome", list(ValidityOutcome))
    def test_same_routes_for_same_outcome(self, outcome: ValidityOutcome) -> None:
        routes = compute_stage_routes(RouteContext("test", outcome.value))
        offered = set(_slugs(routes))
        expected = {self.RECOMMENDATION_TO_SLUG[rec] for rec in self.ALLOWED_PHASE7[outcome]}
        assert offered == expected

    @pytest.mark.parametrize("outcome", [ValidityOutcome.INCONCLUSIVE, ValidityOutcome.INVALIDATED])
    def test_removed_reconciliation_route_is_refused(self, outcome: ValidityOutcome) -> None:
        routes = compute_stage_routes(RouteContext("test", outcome.value))
        assert RouteSlug.RETURN_TO_BUILD in _slugs(routes)
        with pytest.raises(StageRoutesRefused, match="Unknown route"):
            transition_target("test", "return_to_reconciliation")


class TestTransitionTarget:
    def test_review_targets(self) -> None:
        assert transition_target("design", "approve") == "build"
        assert transition_target("build", "approve") == "test"
        assert transition_target("learn", "approve") == COMPLETED
        assert transition_target("design", "request_changes") == "design"
        assert transition_target("build", "reject") == "build"

    def test_test_assessment_targets(self) -> None:
        assert transition_target("test", "advance_to_learn") == "learn"
        assert transition_target("test", "repeat_test") == "test"
        assert transition_target("test", "return_to_build") == "build"
        assert transition_target("test", "return_to_design") == "design"
        assert transition_target("test", "close_cycle") == ABANDONED
        assert transition_target("test", "learn_from_invalidated_evidence") == "learn"

    def test_unknown_route_is_refused(self) -> None:
        with pytest.raises(StageRoutesRefused):
            transition_target("design", "sideways")
        with pytest.raises(StageRoutesRefused):
            transition_target("reconciliation", "approve")
