"""One Test run must produce one verdict, whoever iterates the results.

Test dispatches several workers and more than one can return a complete
``validity_assessment``. Observed live: a ``validity_assessment`` seat failed the
``direction`` check and computed ``invalidated`` while a ``statistical_analysis``
seat on the same run failed nothing and computed ``supported``.

Selection used to be "first complete assessment wins", so the answer depended on
list order — and the two call sites do not share one. The deck built its route
menu from the invalidated outcome and offered "Learn from invalid evidence"; the
gate's own recompute picked the supported one and refused that route with "not
compatible with the server-computed Test outcome". The reviewer was left with a
card whose buttons all failed: an unanswerable human gate at the end of a
complete, correct cycle.

The Test spec names ``VALIDITY_ASSESSMENT`` as its required capability and the
others as optional, so that seat owns the verdict. These tests pin the selection
to the seat rather than to the order.
"""

from __future__ import annotations

from deerflow.agents.dbtl.live_stage.test_review import validated_test_assessment
from deerflow.dbtl.stage_runner import parse_worker_result

#: The seven checks of ``generic-predictive:v2``. Only ``direction`` differs
#: between the two seats below, which is exactly how the live disagreement arose.
_CHECKS = ("fold_composition", "predictive_ceiling", "direction", "leakage", "tester_holdout", "reproducibility", "reconciled_inputs")


def _assessment(*, direction_passes: bool):
    return {
        "metrics": [{"name": "accuracy", "value": 0.28, "threshold": 0.0, "criterion": "gte", "plausible_max": 1.0, "unit": "r"}],
        "checks": [
            {
                "check": name,
                "status": "passed" if (name != "direction" or direction_passes) else "failed",
                "detail": f"{name} examined.",
                "evidence_refs": ["/mnt/user-data/outputs/metrics.json"],
            }
            for name in _CHECKS
        ],
        "limitations": ["Pilot only."],
        "rationale": "Assessed against the pinned pack.",
    }


def _worker(capability: str, *, direction_passes: bool):
    return parse_worker_result(
        {
            "status": "completed",
            "summary": f"{capability} assessed the run.",
            "claims": ["The run was assessed."],
            "evidence_refs": [{"kind": "workspace_file", "reference": "/mnt/user-data/outputs/metrics.json", "description": "metrics"}],
            "limitations": ["Pilot only."],
            "quality_checks": [{"name": "assessment complete", "passed": True, "detail": "All seven pack checks reported.", "evidence_refs": ["/mnt/user-data/outputs/metrics.json"]}],
            "recommended_next_actions": ["Record the outcome."],
            "provenance": {"tools_used": ["read_file"], "validity_assessment": _assessment(direction_passes=direction_passes)},
        },
        capability=capability,
        agent_name=f"{capability}-seat",
    )


class TestTheVerdictBelongsToTheSeatNotTheListOrder:
    #: `reconciled_inputs` is always replaced from the server-owned lineage, so
    #: without one the check set is short and every assessment is discarded.
    BUILD_TEST = {"build_lineage": {"id": "lineage-1"}}

    def _outcome(self, results):
        assessment = validated_test_assessment(results, build_test=self.BUILD_TEST, rerun=None)
        assert assessment is not None, "a complete assessment was rejected outright"
        return assessment["evaluation"]["outcome"]

    def test_the_validity_seat_wins_when_it_comes_first(self) -> None:
        results = [_worker("validity_assessment", direction_passes=False), _worker("statistical_analysis", direction_passes=True)]
        assert self._outcome(results) == "invalidated"

    def test_the_validity_seat_wins_when_it_comes_last(self) -> None:
        # The live failure: this ordering silently produced the *other* verdict,
        # so the deck and the gate validator disagreed and the card died.
        results = [_worker("statistical_analysis", direction_passes=True), _worker("validity_assessment", direction_passes=False)]
        assert self._outcome(results) == "invalidated"

    def test_both_orderings_agree(self) -> None:
        a = _worker("validity_assessment", direction_passes=False)
        b = _worker("statistical_analysis", direction_passes=True)
        assert self._outcome([a, b]) == self._outcome([b, a]), "the verdict still depends on iteration order"

    def test_a_supporting_seat_is_still_used_when_no_validity_seat_reported(self) -> None:
        # Falling back matters: refusing every non-required seat would turn a
        # missing specialist into a stage with no reviewable evidence at all.
        results = [_worker("statistical_analysis", direction_passes=True)]
        assert self._outcome(results) == "supported"

    def test_an_untrustworthy_validity_seat_does_not_shadow_a_usable_one(self) -> None:
        capped = parse_worker_result(
            {
                "status": "completed",
                "summary": "Ran out of budget mid-assessment.",
                "claims": [],
                "evidence_refs": [],
                "limitations": [],
                "quality_checks": [],
                "recommended_next_actions": [],
                "provenance": {"tools_used": [], "validity_assessment": _assessment(direction_passes=False)},
            },
            capability="validity_assessment",
            agent_name="capped-seat",
            stop_reason="token_capped",
        )
        assert not capped.is_trustworthy
        assert self._outcome([capped, _worker("statistical_analysis", direction_passes=True)]) == "supported"
