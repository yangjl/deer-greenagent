"""Phase 6: the data readiness and reconciliation bridge.

The phase's no-go is "an agent can silently resolve a contradiction or mutate
raw inputs", so the tests are organised around the two halves of that sentence
rather than around the module's functions: what an agent may and may not close,
and what makes the gate notice that the data moved underneath an approval.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.reconciliation import (
    HUMAN_RESOLVED_CHECKS,
    ActorType,
    ApprovalBinding,
    BlockerKind,
    DatasetBinding,
    GateOutcome,
    ReconciliationCheck,
    ReconciliationRefused,
    ReconciliationRow,
    RowStatus,
    apply_resolution,
    check_approval_still_valid,
    dataset_fingerprint,
    evaluate_gate,
    summarize_matrix,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def _dataset(key: str = "yield_2024", content_hash: str = HASH_A, **overrides) -> DatasetBinding:
    payload = {"source_key": key, "uri": f"/mnt/user-data/workspace/{key}.csv", "content_hash": content_hash}
    payload.update(overrides)
    return DatasetBinding(**payload)


def _row(check: ReconciliationCheck = ReconciliationCheck.UNITS_AND_ENCODING, **overrides) -> ReconciliationRow:
    payload = {"row_id": f"row-{check.value}", "check": check, "field_name": "Yield units"}
    payload.update(overrides)
    return ReconciliationRow(**payload)


class TestAgentAuthority:
    """An agent proposes; a person decides — but only where judgement is needed."""

    @pytest.mark.parametrize("check", sorted(HUMAN_RESOLVED_CHECKS))
    def test_an_agent_cannot_close_a_judgement_row(self, check: ReconciliationCheck) -> None:
        row = _row(check, field_name="Treatment coding")
        with pytest.raises(ReconciliationRefused, match="human decision"):
            apply_resolution(row, status=RowStatus.RESOLVED, resolution="WW maps to 0", actor=ActorType.AGENT)

    @pytest.mark.parametrize("check", sorted(HUMAN_RESOLVED_CHECKS))
    def test_an_agent_cannot_waive_a_judgement_row_either(self, check: ReconciliationCheck) -> None:
        row = _row(check)
        with pytest.raises(ReconciliationRefused, match="human decision"):
            apply_resolution(row, status=RowStatus.WAIVED, resolution="out of scope", actor=ActorType.AGENT)

    def test_an_agent_may_close_an_arithmetic_row(self) -> None:
        # Demanding a signature on a unit conversion would train reviewers to
        # click through the whole matrix, which is how the real decisions get
        # missed.
        row = _row(ReconciliationCheck.UNITS_AND_ENCODING)
        updated = apply_resolution(row, status=RowStatus.RESOLVED, resolution="bu/ac converted to Mg/ha", actor=ActorType.AGENT)
        assert updated.status is RowStatus.RESOLVED
        assert updated.resolved_by_actor is ActorType.AGENT

    def test_an_agent_may_always_report_a_blocker(self) -> None:
        # A blocker only ever makes the gate stricter, so restricting it would
        # suppress exactly the finding the worker was dispatched to make.
        row = _row(ReconciliationCheck.CONTRADICTORY_SOURCES, field_name="Hybrid ID")
        updated = apply_resolution(
            row,
            status=RowStatus.BLOCKED,
            resolution="19 hybrid IDs in source A have no match in source B",
            actor=ActorType.AGENT,
            blocker_kind=BlockerKind.MISSING_DATA,
        )
        assert updated.status is RowStatus.BLOCKED
        assert updated.blocker_kind is BlockerKind.MISSING_DATA

    def test_a_human_decision_records_who_made_it(self) -> None:
        row = _row(ReconciliationCheck.CONTRADICTORY_SOURCES)
        updated = apply_resolution(
            row,
            status=RowStatus.RESOLVED,
            resolution="Curated pedigree is authoritative",
            actor=ActorType.HUMAN,
            actor_user_id="user-7",
        )
        assert updated.resolved_by_user_id == "user-7"

    def test_an_anonymous_human_decision_is_refused(self) -> None:
        row = _row()
        with pytest.raises(ReconciliationRefused, match="identity"):
            apply_resolution(row, status=RowStatus.RESOLVED, resolution="fine", actor=ActorType.HUMAN)

    def test_a_decision_always_carries_a_rationale(self) -> None:
        row = _row()
        with pytest.raises(ReconciliationRefused, match="rationale"):
            apply_resolution(row, status=RowStatus.RESOLVED, resolution="   ", actor=ActorType.AGENT)

    def test_a_row_cannot_be_reopened_by_a_decision(self) -> None:
        row = _row()
        with pytest.raises(ReconciliationRefused, match="reopen"):
            apply_resolution(row, status=RowStatus.OPEN, resolution="never mind", actor=ActorType.AGENT)

    def test_resolution_returns_a_new_row(self) -> None:
        row = _row()
        updated = apply_resolution(row, status=RowStatus.RESOLVED, resolution="converted", actor=ActorType.AGENT)
        assert row.status is RowStatus.OPEN
        assert updated is not row


class TestGateBlocking:
    """Build cannot begin from any outcome other than ready_for_build."""

    def test_a_proposed_resolution_still_blocks(self) -> None:
        # The whole point of the proposed state: an agent's suggestion is
        # visible work, not a decision.
        rows = [_row(status=RowStatus.PROPOSED, resolution="looks like bu/ac", resolved_by_actor=ActorType.AGENT)]
        gate = evaluate_gate(rows, [_dataset()])
        assert gate.outcome is GateOutcome.CHANGES_REQUIRED
        assert not gate.ready

    def test_an_optional_open_row_does_not_block(self) -> None:
        rows = [_row(required=False)]
        assert evaluate_gate(rows, [_dataset()]).ready

    def test_a_blocked_optional_row_still_blocks(self) -> None:
        # Optional means "we may not need to reconcile this", not "we may ignore
        # a discovered contradiction in it".
        rows = [
            _row(
                required=False,
                status=RowStatus.BLOCKED,
                resolution="sources disagree",
                blocker_kind=BlockerKind.CONFLICTING_SOURCES,
            )
        ]
        assert not evaluate_gate(rows, [_dataset()]).ready

    def test_a_fully_resolved_matrix_reaches_ready_for_build(self) -> None:
        rows = [
            _row(ReconciliationCheck.UNITS_AND_ENCODING, status=RowStatus.RESOLVED, resolution="converted"),
            _row(ReconciliationCheck.IDENTIFIER_INTEGRITY, status=RowStatus.WAIVED, resolution="single source, not applicable"),
        ]
        gate = evaluate_gate(rows, [_dataset()])
        assert gate.outcome is GateOutcome.READY_FOR_BUILD
        assert gate.resolved_count == 2
        assert gate.total_required == 2

    def test_conflicting_sources_outrank_missing_data_in_the_outcome_code(self) -> None:
        # The code a reviewer sees should name the thing that has to be fixed
        # first, not whichever blocker happened to sort first.
        rows = [
            _row(ReconciliationCheck.IDENTIFIER_INTEGRITY, row_id="r1", status=RowStatus.BLOCKED, resolution="19 missing", blocker_kind=BlockerKind.MISSING_DATA),
            _row(ReconciliationCheck.CONTRADICTORY_SOURCES, row_id="r2", status=RowStatus.BLOCKED, resolution="disagree", blocker_kind=BlockerKind.CONFLICTING_SOURCES),
        ]
        assert evaluate_gate(rows, [_dataset()]).outcome is GateOutcome.BLOCKED_CONFLICTING_SOURCES

    def test_missing_data_outranks_an_indeterminate_row(self) -> None:
        rows = [
            _row(ReconciliationCheck.MISSINGNESS, row_id="r1", status=RowStatus.BLOCKED, resolution="unclear", blocker_kind=BlockerKind.INDETERMINATE),
            _row(ReconciliationCheck.IDENTIFIER_INTEGRITY, row_id="r2", status=RowStatus.BLOCKED, resolution="19 missing", blocker_kind=BlockerKind.MISSING_DATA),
        ]
        assert evaluate_gate(rows, [_dataset()]).outcome is GateOutcome.BLOCKED_MISSING_DATA

    def test_an_indeterminate_blocker_is_inconclusive_not_a_failure(self) -> None:
        rows = [_row(status=RowStatus.BLOCKED, resolution="cannot tell", blocker_kind=BlockerKind.INDETERMINATE)]
        assert evaluate_gate(rows, [_dataset()]).outcome is GateOutcome.INCONCLUSIVE_DATA

    def test_no_declared_sources_cannot_be_ready(self) -> None:
        assert not evaluate_gate([], []).ready

    def test_declared_sources_without_a_matrix_cannot_be_ready(self) -> None:
        gate = evaluate_gate([], [_dataset()])
        assert not gate.ready
        assert gate.outcome is GateOutcome.CHANGES_REQUIRED
        assert any("No reconciliation rows" in reason for reason in gate.reasons)

    def test_unreadable_rows_fail_closed(self) -> None:
        gate = evaluate_gate(
            [_row(status=RowStatus.RESOLVED, resolution="converted")],
            [_dataset()],
            unreadable_row_ids=("corrupt-row",),
        )
        assert not gate.ready
        assert "corrupt-row" in gate.blocking_rows
        assert any("could not be read" in reason for reason in gate.reasons)

    def test_a_mutable_raw_source_blocks_the_gate(self) -> None:
        # "Raw data remains unchanged" is a gate condition, not a convention.
        gate = evaluate_gate([], [_dataset(declared_immutable=False)])
        assert not gate.ready
        assert any("immutable" in reason for reason in gate.reasons)

    def test_a_mutable_derived_source_does_not_block(self) -> None:
        rows = [_row(status=RowStatus.RESOLVED, resolution="derived output verified")]
        assert evaluate_gate(rows, [_dataset(role="derived", declared_immutable=False)]).ready

    def test_an_unapproved_design_blocks_reconciliation(self) -> None:
        gate = evaluate_gate([], [_dataset()], design_approved=False)
        assert not gate.ready
        assert any("Design" in reason for reason in gate.reasons)

    def test_reasons_name_the_field_a_reviewer_must_look_at(self) -> None:
        rows = [_row(field_name="Hybrid ID", status=RowStatus.BLOCKED, resolution="19 missing", blocker_kind=BlockerKind.MISSING_DATA)]
        gate = evaluate_gate(rows, [_dataset()])
        assert any(reason.startswith("Hybrid ID:") for reason in gate.reasons)


class TestDatasetFingerprint:
    """A dataset change must be detectable; a reordering must not be."""

    def test_declaration_order_does_not_change_the_fingerprint(self) -> None:
        one = _dataset("a", HASH_A)
        two = _dataset("b", HASH_B)
        assert dataset_fingerprint([one, two]) == dataset_fingerprint([two, one])

    def test_a_changed_content_hash_changes_the_fingerprint(self) -> None:
        assert dataset_fingerprint([_dataset(content_hash=HASH_A)]) != dataset_fingerprint([_dataset(content_hash=HASH_B)])

    def test_source_identity_and_immutability_are_bound(self) -> None:
        baseline = _dataset()
        moved = _dataset(uri="/mnt/user-data/workspace/replaced.csv")
        made_writable = _dataset(declared_immutable=False)
        assert dataset_fingerprint([baseline]) != dataset_fingerprint([moved])
        assert dataset_fingerprint([baseline]) != dataset_fingerprint([made_writable])

    def test_an_added_source_changes_the_fingerprint(self) -> None:
        assert dataset_fingerprint([_dataset("a")]) != dataset_fingerprint([_dataset("a"), _dataset("b", HASH_B)])

    def test_a_non_sha256_hash_is_refused(self) -> None:
        with pytest.raises(ReconciliationRefused, match="SHA-256"):
            DatasetBinding(source_key="x", uri="/x.csv", content_hash="not-a-hash")


class TestApprovalInvalidation:
    """If a dataset changes, the gate is invalidated and must be rerun."""

    def _binding(self, **overrides) -> ApprovalBinding:
        payload = {"dataset_fingerprint": "fp-1", "stage_spec_key": "generic:reconciliation:v1", "policy_version": "p1"}
        payload.update(overrides)
        return ApprovalBinding(**payload)

    def test_an_unchanged_world_keeps_the_approval(self) -> None:
        check = check_approval_still_valid(
            self._binding(),
            current_fingerprint="fp-1",
            current_spec_key="generic:reconciliation:v1",
            current_policy_version="p1",
        )
        assert not check.invalidated

    def test_a_changed_dataset_invalidates_the_approval(self) -> None:
        check = check_approval_still_valid(
            self._binding(),
            current_fingerprint="fp-2",
            current_spec_key="generic:reconciliation:v1",
            current_policy_version="p1",
        )
        assert check.invalidated
        assert any("dataset changed" in reason for reason in check.reasons)

    def test_a_moved_stage_contract_invalidates_the_approval(self) -> None:
        check = check_approval_still_valid(
            self._binding(),
            current_fingerprint="fp-1",
            current_spec_key="generic:reconciliation:v2",
            current_policy_version="p1",
        )
        assert check.invalidated

    def test_a_moved_policy_version_invalidates_the_approval(self) -> None:
        check = check_approval_still_valid(
            self._binding(),
            current_fingerprint="fp-1",
            current_spec_key="generic:reconciliation:v1",
            current_policy_version="p2",
        )
        assert check.invalidated

    def test_an_unrecorded_binding_is_treated_as_invalidated(self) -> None:
        # An approval nobody can pin to a dataset set is an approval nobody can
        # check. Defaulting to "still fine" would carry pre-Phase-6 records into
        # Build unchallenged.
        check = check_approval_still_valid(None, current_fingerprint="fp-1", current_spec_key="k", current_policy_version="p")
        assert check.invalidated


class TestMatrixIntegrity:
    """Shapes the matrix must not be able to take."""

    def test_a_blocked_row_must_name_its_blocker_kind(self) -> None:
        with pytest.raises(ReconciliationRefused, match="blocker kind"):
            ReconciliationRow(row_id="r", check=ReconciliationCheck.MISSINGNESS, field_name="f", status=RowStatus.BLOCKED, resolution="why")

    def test_a_settled_row_must_carry_a_rationale(self) -> None:
        with pytest.raises(ReconciliationRefused, match="rationale"):
            ReconciliationRow(row_id="r", check=ReconciliationCheck.MISSINGNESS, field_name="f", status=RowStatus.RESOLVED)

    def test_every_check_has_a_label_for_the_matrix(self) -> None:
        # Status is never conveyed by colour alone, so every check needs a word.
        from deerflow.dbtl.reconciliation import CHECK_LABELS

        assert set(CHECK_LABELS) == set(ReconciliationCheck)

    def test_summary_counts_every_status(self) -> None:
        rows = [_row(row_id="a"), _row(row_id="b", status=RowStatus.RESOLVED, resolution="ok")]
        counts = summarize_matrix(rows)
        assert counts["open"] == 1
        assert counts["resolved"] == 1
        assert counts["blocked"] == 0
