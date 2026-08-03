"""What shape a Build worker's `summary` may arrive in.

A validation phase returned a complete, well-formed result -- 28 of 28
mandatory fidelity checks passed, claims bound to evidence, artifacts written
-- and reported its `summary` as a metrics object rather than a sentence. The
whole phase was discarded: 344,504 input tokens of sandbox work thrown away
over the JSON type of one field, and the refusal read "The worker's result
must report a 'summary'" about a result that plainly reported one.

Two things are wrong there and they are separable. The message misdiagnosed an
existing field as a missing one, which sends a reader looking for the wrong
thing. And Build already has a compatibility boundary for exactly this class of
drift -- descriptive `kind` labels on workspace files, a compact boolean
`quality_checks` map -- because discarding real work over a shape a model
commonly emits is the waste the whole step chain exists to prevent.

The boundary stays narrow in the same way it already does: Build only, rendered
from the worker's own reported values, and never able to manufacture a claim, a
check, or a piece of evidence.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.worker_result import WorkerResultRejected, WorkerStatus, parse_worker_result


def _payload(**overrides) -> dict:
    payload = {
        "status": "completed",
        "summary": "Recomputed the simulated dataset independently.",
        "claims": ["All mandatory fidelity checks passed."],
        "evidence_refs": [{"kind": "workspace_file", "reference": "/mnt/user-data/outputs/report.json", "description": "validation table"}],
        "limitations": [],
        "quality_checks": [{"name": "checks executed", "passed": True, "detail": ""}],
        "recommended_next_actions": ["Review the validation report."],
        "provenance": {"tools_used": ["bash"]},
    }
    payload.update(overrides)
    return payload


def _parse(payload: dict, *, stage: str | None = "build"):
    return parse_worker_result(payload, capability="statistical_analysis", agent_name="general-purpose", stage=stage)


class TestAMetricsObjectSummaryIsReadRatherThanDiscarded:
    """The live failure, and the shape it actually arrived in."""

    #: Trimmed from the run that was thrown away.
    LIVE_SUMMARY = {
        "mandatory_checks_total": 28,
        "mandatory_checks_passed": 28,
        "mandatory_checks_failed": 0,
        "failed_checks": [],
        "individuals": 1000,
        "markers": 1000,
        "realized_heritability": 0.5,
    }

    def test_the_phase_is_accepted(self) -> None:
        result = _parse(_payload(summary=self.LIVE_SUMMARY))

        assert result.status is WorkerStatus.COMPLETED
        assert result.is_trustworthy

    def test_the_reported_values_survive_into_readable_text(self) -> None:
        result = _parse(_payload(summary=self.LIVE_SUMMARY))

        assert isinstance(result.summary, str)
        # A reviewer reads this. The numbers the worker measured have to be in
        # it, or a rendered summary is worse than the refusal it replaced.
        assert "mandatory_checks_passed" in result.summary
        assert "28" in result.summary
        assert "realized_heritability" in result.summary

    def test_an_empty_object_is_not_a_summary(self) -> None:
        # Nothing to render is the same as nothing reported, and quietly
        # accepting it would let a phase satisfy the contract by saying nothing.
        with pytest.raises(WorkerResultRejected):
            _parse(_payload(summary={}))


class TestTheBoundaryStaysWhereItIs:
    """Compatibility must not spread past the stage that asked for it."""

    def test_another_stage_still_requires_prose(self) -> None:
        with pytest.raises(WorkerResultRejected):
            _parse(_payload(summary={"checks": 28}), stage="test")

    def test_a_stageless_parse_still_requires_prose(self) -> None:
        with pytest.raises(WorkerResultRejected):
            _parse(_payload(summary={"checks": 28}), stage=None)

    def test_a_list_summary_is_still_refused_on_build(self) -> None:
        # Only a mapping is normalized. A list has no field names, so rendering
        # it would produce numbers a reader cannot attribute to anything.
        with pytest.raises(WorkerResultRejected):
            _parse(_payload(summary=[1, 2, 3]))


class TestTheRefusalNamesWhatIsActuallyWrong:
    """A misdiagnosis sends a reader looking for the wrong thing."""

    def test_a_missing_summary_is_reported_as_missing(self) -> None:
        payload = _payload()
        payload.pop("summary")

        with pytest.raises(WorkerResultRejected, match="must report a 'summary'"):
            _parse(payload)

    def test_a_wrongly_typed_summary_is_not_reported_as_missing(self) -> None:
        with pytest.raises(WorkerResultRejected) as excinfo:
            _parse(_payload(summary=[1, 2, 3]))

        message = str(excinfo.value)
        assert "must report a 'summary'" not in message
        assert "list" in message
