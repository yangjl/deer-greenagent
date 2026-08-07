"""Test as a conditional stage, entered through an explicit human disposition.

Today an approved Build always opens Test, and Learn cannot run until Test has
produced a human-owned validity assessment. That is the right rule for a
confirmatory claim and the wrong one for the exploratory work this product
mostly serves: a descriptive figure has no folds, no holdout, and no predictive
ceiling, so it fails a contract it never claimed to satisfy and can never reach
Learn at all.

The fix is not to weaken Test. It is to let a person say which of two things
they are doing, and to record that decision:

    Keep and validate      → Build → Test, exactly as before
    Learn from exploration → Build → Learn, with Test explicitly skipped

Four properties are what make skipping safe rather than convenient, and each
has a test here:

1. **The default is unchanged.** A caller that has not been taught about the
   disposition still gets the mandatory path, so this cannot alter a cycle
   nobody opted in.
2. **Skipped is its own status.** ``locked`` means work is still blocked and
   ``approved`` means a qualification happened; a skip is neither, and
   collapsing it into either one is how "we chose not to validate this" becomes
   indistinguishable from "this passed".
3. **Only a human choice may skip.** The disposition is refused anywhere it
   could be taken as a default — a stage it does not belong to, a verdict that
   is not an approval, and a Test that has already started.
4. **The skip is stepped over, never stepped around.** Learn opens from Build,
   and the machine still refuses Learn when nothing settled Test at all.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.cycle_state import (
    BuildDisposition,
    ReviewDecision,
    StageStatus,
    TransitionRefused,
    apply_review,
    can_enter_stage,
    initial_stage_statuses,
    next_cycle_state,
)
from deerflow.dbtl.stage_routes import RouteContext, RouteSlug, compute_stage_routes, transition_target


def _built(**overrides: StageStatus) -> dict[str, StageStatus]:
    """A cycle whose Design, Reconciliation, and Build work is done."""
    statuses = initial_stage_statuses()
    statuses.update(
        design=StageStatus.APPROVED,
        reconciliation=StageStatus.APPROVED,
        build=StageStatus.AWAITING_REVIEW,
    )
    statuses.update(overrides)
    return statuses


class TestTheMandatoryPathIsStillTheDefault:
    def test_build_approval_opens_test_when_no_disposition_is_given(self):
        updated = apply_review(_built(), "build", ReviewDecision.APPROVE)

        assert updated["test"] is StageStatus.IN_PROGRESS
        assert updated["learn"] is StageStatus.LOCKED

    def test_keep_and_validate_is_the_same_as_saying_nothing(self):
        explicit = apply_review(_built(), "build", ReviewDecision.APPROVE, build_disposition=BuildDisposition.KEEP_AND_VALIDATE)
        implicit = apply_review(_built(), "build", ReviewDecision.APPROVE)

        assert explicit == implicit

    def test_learn_is_refused_while_test_is_merely_open(self):
        statuses = _built(build=StageStatus.APPROVED, test=StageStatus.IN_PROGRESS)

        assert can_enter_stage("learn", "test", statuses) is False

    def test_a_cycle_in_build_cannot_reach_learn_without_settling_test(self):
        statuses = _built(build=StageStatus.APPROVED)

        assert can_enter_stage("learn", "build", statuses) is False


class TestSkippedIsItsOwnStatus:
    def test_skipping_records_neither_locked_nor_approved(self):
        updated = apply_review(_built(), "build", ReviewDecision.APPROVE, build_disposition=BuildDisposition.LEARN_EXPLORATORY)

        assert updated["test"] is StageStatus.SKIPPED
        assert updated["test"] is not StageStatus.LOCKED
        assert updated["test"] is not StageStatus.APPROVED

    def test_skipping_opens_learn(self):
        updated = apply_review(_built(), "build", ReviewDecision.APPROVE, build_disposition=BuildDisposition.LEARN_EXPLORATORY)

        assert updated["build"] is StageStatus.APPROVED
        assert updated["learn"] is StageStatus.IN_PROGRESS

    def test_a_skipped_test_does_not_read_as_a_completed_stage(self):
        assert StageStatus.SKIPPED.value == "skipped"


class TestOnlyAHumanChoiceMaySkip:
    def test_a_disposition_on_another_stage_is_refused(self):
        statuses = initial_stage_statuses()
        statuses["design"] = StageStatus.AWAITING_REVIEW

        with pytest.raises(TransitionRefused, match="disposition"):
            apply_review(statuses, "design", ReviewDecision.APPROVE, build_disposition=BuildDisposition.LEARN_EXPLORATORY)

    def test_a_disposition_on_a_rejection_is_refused(self):
        with pytest.raises(TransitionRefused, match="disposition"):
            apply_review(_built(), "build", ReviewDecision.REJECT, build_disposition=BuildDisposition.LEARN_EXPLORATORY)

    def test_a_disposition_on_a_change_request_is_refused(self):
        with pytest.raises(TransitionRefused, match="disposition"):
            apply_review(_built(), "build", ReviewDecision.REQUEST_CHANGES, build_disposition=BuildDisposition.LEARN_EXPLORATORY)

    def test_test_cannot_be_skipped_once_it_has_started(self):
        statuses = _built(test=StageStatus.IN_PROGRESS)

        with pytest.raises(TransitionRefused, match="already"):
            apply_review(statuses, "build", ReviewDecision.APPROVE, build_disposition=BuildDisposition.LEARN_EXPLORATORY)

    def test_a_completed_qualification_cannot_be_retroactively_skipped(self):
        statuses = _built(test=StageStatus.APPROVED)

        with pytest.raises(TransitionRefused, match="already"):
            apply_review(statuses, "build", ReviewDecision.APPROVE, build_disposition=BuildDisposition.LEARN_EXPLORATORY)


class TestTheSkipIsSteppedOver:
    def test_the_cycle_advances_from_build_to_learn(self):
        statuses = _built(build=StageStatus.APPROVED, test=StageStatus.SKIPPED, learn=StageStatus.IN_PROGRESS)

        assert next_cycle_state("build", statuses) == "learn"

    def test_learn_may_be_entered_directly_from_build(self):
        statuses = _built(build=StageStatus.APPROVED, test=StageStatus.SKIPPED, learn=StageStatus.IN_PROGRESS)

        assert can_enter_stage("learn", "build", statuses) is True

    def test_learn_still_completes_the_cycle(self):
        statuses = _built(build=StageStatus.APPROVED, test=StageStatus.SKIPPED, learn=StageStatus.APPROVED)

        assert next_cycle_state("learn", statuses) == "completed"

    def test_a_skipped_test_cannot_itself_be_entered(self):
        statuses = _built(build=StageStatus.APPROVED, test=StageStatus.SKIPPED)

        assert can_enter_stage("test", "build", statuses) is False

    def test_a_skipped_test_is_not_reviewable(self):
        statuses = _built(build=StageStatus.APPROVED, test=StageStatus.SKIPPED)

        with pytest.raises(TransitionRefused):
            apply_review(statuses, "test", ReviewDecision.APPROVE)


class TestTheRouteMenuOffersTheChoice:
    def test_an_approved_build_offers_the_exploratory_route(self):
        routes = compute_stage_routes(RouteContext(stage="build", outcome="approved", conditional_test=True))

        exploratory = next(route for route in routes if route.slug == RouteSlug.LEARN_EXPLORATORY)
        assert exploratory.to_stage == "learn"

    def test_the_route_is_absent_by_default(self):
        routes = compute_stage_routes(RouteContext(stage="build", outcome="approved"))

        assert RouteSlug.LEARN_EXPLORATORY not in {route.slug for route in routes}

    def test_qualifying_stays_on_the_menu_beside_it(self):
        routes = compute_stage_routes(RouteContext(stage="build", outcome="approved", conditional_test=True))

        slugs = [route.slug for route in routes]
        assert slugs.index(RouteSlug.ADVANCE) < slugs.index(RouteSlug.LEARN_EXPLORATORY)

    def test_no_other_stage_offers_it(self):
        for stage in ("design", "learn"):
            routes = compute_stage_routes(RouteContext(stage=stage, outcome="approved", conditional_test=True))
            assert RouteSlug.LEARN_EXPLORATORY not in {route.slug for route in routes}

    def test_a_build_that_was_not_approved_does_not_offer_it(self):
        routes = compute_stage_routes(RouteContext(stage="build", outcome="changes_requested", conditional_test=True))

        assert RouteSlug.LEARN_EXPLORATORY not in {route.slug for route in routes}

    def test_the_recorded_edge_points_at_learn(self):
        assert transition_target("build", "learn_exploratory") == "learn"

    def test_the_edge_is_refused_from_any_other_stage(self):
        for stage in ("design", "test", "learn"):
            with pytest.raises(Exception):
                transition_target(stage, "learn_exploratory")


class TestTheIntentIsOwnedByBuildAlone:
    """Rendering a control is not authorization; the matrix is.

    A new intent that reaches the matrix without a router branch passes
    validation and then falls through to ``review_stage`` with a verdict it
    cannot parse, so the stage scoping is checked here rather than assumed
    from the button that emits it.
    """

    def test_build_may_record_it(self):
        from deerflow.dbtl.stage_feedback import validate_stage_feedback_intent

        validate_stage_feedback_intent("build", "learn_exploratory")

    @pytest.mark.parametrize("stage", ["design", "test", "learn"])
    def test_no_other_stage_may(self, stage):
        from deerflow.dbtl.stage_feedback import validate_stage_feedback_intent

        with pytest.raises(ValueError, match="not allowed"):
            validate_stage_feedback_intent(stage, "learn_exploratory")

    def test_it_shares_the_single_use_review_group(self):
        """It *is* the Build verdict, so a reviewer records one decision about
        a Build rather than an approval plus a second thought about it."""
        from deerflow.persistence.dbtl.design_feedback_ops import _ACTION_GROUP

        assert _ACTION_GROUP["learn_exploratory"] == _ACTION_GROUP["approve"] == "stage_review"

    def test_the_read_model_offers_it_only_where_it_is_enabled(self, monkeypatch):
        from app.gateway.routers import dbtl_cycles

        monkeypatch.setattr(dbtl_cycles, "conditional_test_enabled", lambda: True)
        assert dbtl_cycles._exploratory_actions("build") == ["learn_exploratory"]
        assert dbtl_cycles._exploratory_actions("learn") == []

        monkeypatch.setattr(dbtl_cycles, "conditional_test_enabled", lambda: False)
        assert dbtl_cycles._exploratory_actions("build") == []

    def test_the_deck_renders_the_control_for_build_only(self):
        from deerflow.dbtl.council_deck import render_stage_review_controls

        assert 'data-deck-action="learn_exploratory"' in render_stage_review_controls("build", None)
        for stage in ("test", "learn"):
            assert 'data-deck-action="learn_exploratory"' not in render_stage_review_controls(stage, None)

    def test_the_rendered_control_ships_inert(self):
        """The persisted file must be safe to open from anywhere; activation
        is the authenticated parent's job."""
        from deerflow.dbtl.council_deck import render_stage_review_controls

        markup = render_stage_review_controls("build", None)
        button = markup.split('data-deck-action="learn_exploratory"')[1].split(">")[0]
        assert "disabled" in button


class TestSkippingWorksWithoutReconciliation:
    """The two optional-stage rules are independent and must compose."""

    def test_a_design_only_cycle_can_still_close_exploratively(self):
        statuses = initial_stage_statuses()
        statuses.update(design=StageStatus.APPROVED, build=StageStatus.AWAITING_REVIEW)

        updated = apply_review(
            statuses,
            "build",
            ReviewDecision.APPROVE,
            build_disposition=BuildDisposition.LEARN_EXPLORATORY,
        )

        assert updated["test"] is StageStatus.SKIPPED
        assert updated["learn"] is StageStatus.IN_PROGRESS
        assert next_cycle_state("build", updated) == "learn"
