"""A figure format that cannot hash twice must fail in Build, not at Test.

Test re-runs the recorded command in a fresh workspace and requires every
``expected_outputs`` entry to match its approved Build hash byte for byte. PDF,
SVG, PS and EPS each embed a creation timestamp, so the same script renders
different bytes on every run.

Left uncaught, that costs a full Build **and** a full Test, ends in
``invalidated`` with reason ``irreproducible_execution``, and points a reader at
the science when the defect is a file extension. Caught during the phase, the
worker still has its one fresh correction and the fix is one argument to
``savefig``.

The determinism claim these tests rest on is measured, not assumed, in
``test_dbtl_plot_style.py``.
"""

from __future__ import annotations

import pytest

from deerflow.agents.dbtl.live_stage.build_phases import (
    NON_REPRODUCIBLE_OUTPUT_SUFFIXES,
    non_reproducible_rerun_outputs,
    phase_completion_error,
)
from deerflow.dbtl.stage_runner import parse_worker_result

DONE = "model.bin exists"


def _result(expected_outputs, *, status="completed"):
    return parse_worker_result(
        {
            "status": status,
            "summary": "Fitted the model and drew the diagnostics.",
            "claims": ["The model fits."],
            "evidence_refs": [{"kind": "workspace_file", "reference": "outputs/model.bin", "description": "fitted model"}],
            "limitations": ["Pilot only."],
            "quality_checks": [{"name": DONE, "passed": True, "detail": "model.bin is present.", "evidence_refs": ["outputs/model.bin"]}],
            "recommended_next_actions": ["Test it."],
            "provenance": {
                "tools_used": ["bash"],
                "rerun_spec": {
                    "version": 1,
                    "entry_point": "outputs/run.py",
                    "command": "python run.py",
                    "seed": "2026",
                    "inputs": [],
                    "environment": {"python": "3.12"},
                    "configuration": [],
                    "expected_outputs": list(expected_outputs),
                },
            },
        },
        capability="software_engineering",
        agent_name="build-engineer",
    )


class TestANonReproducibleFigureFormatFailsItsPhase:
    def test_a_png_only_build_is_untouched(self) -> None:
        assert phase_completion_error(_result(["outputs/model.bin", "outputs/fit.png"]), DONE) == ""

    @pytest.mark.parametrize("suffix", sorted(NON_REPRODUCIBLE_OUTPUT_SUFFIXES))
    def test_every_timestamped_format_is_refused(self, suffix: str) -> None:
        error = phase_completion_error(_result(["outputs/model.bin", f"outputs/fit{suffix}"]), DONE)
        assert f"outputs/fit{suffix}" in error, f"{suffix} was not named in the refusal"
        assert "PNG" in error, "the refusal must say what to do instead"

    def test_the_refusal_names_every_offender_not_just_the_first(self) -> None:
        error = phase_completion_error(_result(["outputs/a.pdf", "outputs/b.svg", "outputs/c.png"]), DONE)
        assert "outputs/a.pdf" in error and "outputs/b.svg" in error
        assert "outputs/c.png" not in error

    def test_case_and_duplicates_do_not_slip_through(self) -> None:
        found = non_reproducible_rerun_outputs({"expected_outputs": ["outputs/A.PDF", "outputs/A.PDF", "outputs/b.Svg"]})
        assert found == ("outputs/A.PDF", "outputs/b.Svg")

    def test_a_malformed_or_absent_rerun_spec_is_not_this_check_s_business(self) -> None:
        # `structured_rerun_spec` already owns a missing or unparseable record.
        # Reporting it here too would give one refusal two different voices.
        assert non_reproducible_rerun_outputs(None) == ()
        assert non_reproducible_rerun_outputs({}) == ()
        assert non_reproducible_rerun_outputs({"expected_outputs": "outputs/fit.pdf"}) == ()
        assert non_reproducible_rerun_outputs({"expected_outputs": [None, 7]}) == ()

    def test_an_already_failed_phase_keeps_its_own_reason(self) -> None:
        # A failed worker's own account of what went wrong is more useful than a
        # format complaint, and this check must not overwrite it.
        error = phase_completion_error(_result(["outputs/fit.pdf"], status="failed"), DONE)
        assert "cannot reproduce" not in error


class TestTheWorkerIsToldBeforeItDeclares:
    def test_the_rerun_contract_names_the_formats_and_the_consequence(self) -> None:
        from deerflow.agents.dbtl.live_stage.build_phases import BUILD_RERUN_RESULT_NOTE

        assert ".pdf" in BUILD_RERUN_RESULT_NOTE and ".svg" in BUILD_RERUN_RESULT_NOTE
        assert "byte for byte" in BUILD_RERUN_RESULT_NOTE
