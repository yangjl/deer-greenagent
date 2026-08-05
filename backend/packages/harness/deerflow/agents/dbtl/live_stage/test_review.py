"""Typed Test assessment reconstruction owned by the server."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig

from deerflow.agents.dbtl.live_stage.test_rerun import TestRerunRecord, TestRerunStatus, parse_test_rerun_record
from deerflow.dbtl.cycle_state import StageStatus
from deerflow.dbtl.reconciliation_policy import reconciliation_required
from deerflow.dbtl.stage_meetings import review_meeting_recorded, surface_meeting_gate
from deerflow.dbtl.stage_spec import StageSpecNotFound, resolve_spec_by_key
from deerflow.dbtl.validity import (
    DEFAULT_VALIDITY_PACK,
    CheckStatus,
    HeadlineMetric,
    ValidityCheck,
    ValidityCheckName,
    evaluate_validity,
)
from deerflow.dbtl.worker_result import StageWorkerResult, WorkerResultRejected, parse_worker_result


def validated_test_assessment(
    results: Sequence[StageWorkerResult],
    *,
    build_test: Mapping[str, Any] | None,
    rerun: TestRerunRecord | None = None,
) -> dict[str, Any] | None:
    """Return the first complete Test assessment under the pinned pack.

    Workers calculate typed metrics and checks; the server reconstructs those
    values and deterministically computes the outcome and legal routes.
    """
    required = {item.value for item in DEFAULT_VALIDITY_PACK.required_checks}
    lineage = dict((build_test or {}).get("build_lineage") or {})
    for result in results:
        if not result.is_trustworthy:
            continue
        raw = result.provenance.get("validity_assessment")
        if not isinstance(raw, Mapping):
            continue
        raw_metrics = raw.get("metrics")
        raw_checks = raw.get("checks")
        if not isinstance(raw_metrics, Sequence) or isinstance(raw_metrics, (str, bytes)):
            continue
        if not isinstance(raw_checks, Sequence) or isinstance(raw_checks, (str, bytes)):
            continue
        try:
            metrics = [HeadlineMetric(**dict(item)) for item in raw_metrics if isinstance(item, Mapping)]
            checks = [ValidityCheck(**dict(item)) for item in raw_checks if isinstance(item, Mapping)]
            if rerun is not None:
                checks = [item for item in checks if item.check is not ValidityCheckName.REPRODUCIBILITY]
                rerun_status = {
                    TestRerunStatus.PASSED: CheckStatus.PASSED,
                    TestRerunStatus.FAILED: CheckStatus.FAILED,
                    TestRerunStatus.MISSING: CheckStatus.MISSING,
                }[rerun.status]
                rerun_evidence = tuple(str(item.get("path") or "") for item in (*rerun.logs, *rerun.outputs) if str(item.get("path") or ""))
                checks.append(
                    ValidityCheck(
                        check=ValidityCheckName.REPRODUCIBILITY,
                        status=rerun_status,
                        detail=rerun.reason,
                        evidence_refs=rerun_evidence,
                    )
                )
            if not reconciliation_required():
                checks = [item for item in checks if item.check is not ValidityCheckName.RECONCILED_INPUTS]
                if lineage:
                    checks.append(
                        ValidityCheck(
                            check=ValidityCheckName.RECONCILED_INPUTS,
                            status=CheckStatus.PASSED,
                            detail="The server recorded immutable input provenance in the approved Build lineage.",
                            evidence_refs=(str(lineage.get("id") or lineage.get("dataset_fingerprint") or "build_lineage"),),
                        )
                    )
            if {item.check.value for item in checks} != required or not metrics:
                continue
            evaluation = evaluate_validity(metrics=metrics, checks=checks)
        except (TypeError, ValueError):
            continue
        limitations = raw.get("limitations")
        rationale = raw.get("rationale")
        return {
            "metrics": [item.as_dict() for item in metrics],
            "checks": [item.as_dict() for item in checks],
            "limitations": [str(item).strip() for item in limitations if str(item).strip()][:100] if isinstance(limitations, Sequence) and not isinstance(limitations, (str, bytes)) else list(result.limitations),
            "rationale": str(rationale).strip()[:10_000] if isinstance(rationale, str) and rationale.strip() else result.summary,
            "evaluation": evaluation.as_dict(),
        }
    return None


@dataclass(frozen=True, slots=True)
class TestReviewService:
    """Read Test review state and perform its server-bound human write."""

    repo: Any
    app_config: Any
    runtime_reader: Callable[[RunnableConfig], dict[str, Any]]

    async def snapshot(self, *, project_id: str, cycle_id: str) -> dict[str, Any] | None:
        cycle = await self.repo.get_cycle(cycle_id, project_id=project_id)
        if cycle is None:
            return None
        test = next((item for item in cycle.get("stages", []) if item.get("stage") == "test"), None)
        if not isinstance(test, Mapping) or str(test.get("status") or "") != StageStatus.AWAITING_REVIEW.value:
            return None
        stored = await self.repo.list_worker_runs(cycle_id, project_id=project_id, stage="test")
        parsed: list[StageWorkerResult] = []
        rerun: TestRerunRecord | None = None
        latest_rerun_prefix: str | None = None
        # A Test stage may be retried several times before human review. Read
        # newest-first so the snapshot is bound to the worker attempt that
        # produced the latest review artifact, not an older failed diagnostic.
        # Keeping ``rerun or`` below then selects the newest durable rerun, and
        # validated_test_assessment sees the newest typed assessment first.
        for item in reversed(stored):
            raw = item.get("result") if isinstance(item, Mapping) else None
            if not isinstance(raw, Mapping):
                continue
            provenance = dict(raw.get("provenance") or {})
            unit_id = str(item.get("unit_id") or "")
            if latest_rerun_prefix is None and unit_id.endswith("-build-rerun"):
                latest_rerun_prefix = unit_id[: -len("-build-rerun")]
                rerun = parse_test_rerun_record(provenance.get("rerun_execution"))
        for item in reversed(stored):
            raw = item.get("result") if isinstance(item, Mapping) else None
            if not isinstance(raw, Mapping):
                continue
            unit_id = str(item.get("unit_id") or "")
            if latest_rerun_prefix is not None and not unit_id.startswith(f"{latest_rerun_prefix}-"):
                continue
            provenance = dict(raw.get("provenance") or {})
            if "validity_assessment" not in provenance:
                continue
            try:
                parsed.append(
                    parse_worker_result(
                        raw,
                        capability=str(raw.get("capability") or item.get("capability") or "validity_assessment"),
                        agent_name=str(raw.get("agent_name") or item.get("agent_name") or "recorded-worker"),
                        stop_reason=(str(raw.get("stop_reason")) if raw.get("stop_reason") else None),
                    )
                )
            except WorkerResultRejected:
                continue
        build_test = await self.repo.build_test_view(cycle_id, project_id=project_id)
        stage_spec_key = str(test.get("stage_spec_key") or "")
        rerun_required = False
        if stage_spec_key:
            try:
                rerun_required = "server_verified_build_rerun" in resolve_spec_by_key(stage_spec_key).validity_gates
            except StageSpecNotFound:
                return None
        if rerun_required and rerun is None:
            rerun = TestRerunRecord(
                status=TestRerunStatus.MISSING,
                command="",
                reason="The current Test contract has no durable server-owned Build rerun record.",
            )
        assessment = validated_test_assessment(parsed, build_test=build_test, rerun=rerun)
        if assessment is None:
            return None
        artifacts = list(cycle.get("artifacts") or [])
        evidence = max(
            (item for item in artifacts if isinstance(item, Mapping) and item.get("stage_attempt_id") == test.get("id") and item.get("artifact_type") in {"validity_report", "test_report"}),
            key=lambda item: int(item.get("revision") or 0),
            default=None,
        )
        if evidence is None:
            return None
        meeting_completed = review_meeting_recorded(
            stage="test",
            stage_attempt_id=str(test.get("id") or ""),
            artifacts=[item for item in artifacts if isinstance(item, Mapping)],
            evidence_artifact_id=str(evidence.get("id") or ""),
            evidence_artifact_revision=int(evidence.get("revision") or 0),
            evidence_content_hash=str(evidence.get("content_hash") or ""),
        )
        difficulty = "standard"
        surface = await self.repo.latest_stage_feedback_surface(
            project_id=project_id,
            cycle_id=cycle_id,
            stage="test",
            stage_attempt_id=str(test.get("id") or ""),
            mode="stage_review",
        )
        gate_payload = dict((surface or {}).get("decision_request") or {}).get("transition_gate")
        assessed = dict(gate_payload or {}).get("assessment")
        if isinstance(assessed, Mapping):
            difficulty = str(assessed.get("difficulty") or difficulty)
        meetings = getattr(getattr(self.app_config, "dbtl", None), "stage_meetings", None)
        gate = surface_meeting_gate(
            stage="test",
            assessed_difficulty=difficulty,
            enabled=bool(getattr(meetings, "test", False)),
            meeting_completed=meeting_completed,
        )
        return {
            **assessment,
            "cycle_id": cycle_id,
            "expected_db_revision": int(cycle["db_revision"]),
            "stage_attempt_id": str(test.get("id") or ""),
            "evidence_uri": str((evidence or {}).get("uri") or ""),
            "evidence_hash": str((evidence or {}).get("content_hash") or ""),
            "meeting": gate.as_dict() if gate is not None else None,
        }

    async def record_outcome(
        self,
        *,
        project_id: str,
        cycle_id: str,
        snapshot: Mapping[str, Any],
        recommendation: str,
        config: RunnableConfig,
        idempotency_key: str,
    ) -> dict[str, Any]:
        runtime = self.runtime_reader(config)
        user_id = str(runtime.get("user_id") or "")
        project_role = str(runtime.get("project_role") or "")
        if not user_id or project_role not in {"owner", "admin", "member"}:
            raise RuntimeError("Authenticated human project membership is required to record a Test decision.")
        fresh = await self.snapshot(project_id=project_id, cycle_id=cycle_id)
        if fresh is None:
            raise RuntimeError("Test is no longer awaiting a decision with complete typed evidence.")
        if str(fresh.get("stage_attempt_id") or "") != str(snapshot.get("stage_attempt_id") or "") or str(fresh.get("evidence_hash") or "") != str(snapshot.get("evidence_hash") or ""):
            raise RuntimeError("The Test evidence changed after this chat card was issued; review the new card.")
        if dict(fresh.get("meeting") or {}).get("transition_routes_locked") is True:
            raise RuntimeError("The required Test review meeting must finish before an outcome can be recorded.")
        current = await self.repo.get_cycle(cycle_id, project_id=project_id)
        if current is None:
            raise RuntimeError("The selected cycle is no longer available.")
        return await self.repo.record_validity_assessment(
            cycle_id=cycle_id,
            project_id=project_id,
            metrics=[dict(item) for item in fresh.get("metrics", []) if isinstance(item, Mapping)],
            checks=[dict(item) for item in fresh.get("checks", []) if isinstance(item, Mapping)],
            recommendation=recommendation,
            limitations=[str(item) for item in fresh.get("limitations", [])],
            rationale=(
                "Human selected "
                f"{recommendation.replace('_', ' ')} from the Test chat card for the "
                f"server-computed {str(dict(fresh.get('evaluation') or {}).get('outcome') or 'unknown').replace('_', ' ')} outcome. "
                f"Evidence assessment: {str(fresh.get('rationale') or 'No additional worker rationale was recorded.')}"
            )[:10_000],
            reviewer_user_id=user_id,
            reviewer_project_role=project_role,
            expected_db_revision=int(current["db_revision"]),
            idempotency_key=idempotency_key,
        )
