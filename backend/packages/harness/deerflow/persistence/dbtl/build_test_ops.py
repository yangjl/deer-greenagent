"""Durable Phase 7 Build lineage and Test validity operations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from deerflow.dbtl.build_execution import parse_rerun_spec
from deerflow.dbtl.cycle_state import StageStatus
from deerflow.dbtl.reconciliation_policy import degraded_evidence_continuation_enabled
from deerflow.dbtl.stage_routes import (
    RouteContext,
    RouteSlug,
    compute_stage_routes,
)
from deerflow.dbtl.stage_spec import resolve_spec_by_key, resolve_stage_spec
from deerflow.dbtl.validity import (
    DEFAULT_VALIDITY_PACK,
    CheckStatus,
    HeadlineMetric,
    ValidityCheck,
    ValidityCheckName,
    ValidityOutcome,
    ValidityRefused,
    WorkflowRecommendation,
    evaluate_validity,
    validate_recommendation,
)
from deerflow.persistence.dbtl.model import (
    DbtlArtifactRow,
    DbtlBuildLineageRow,
    DbtlReviewRow,
    DbtlStageAttemptRow,
    DbtlValidityAssessmentRow,
)


def _sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _build_input_fingerprint(input_artifacts: list[str]) -> str:
    """Fingerprint the exact server-bound inputs examined by Build."""
    unbound = [item for item in input_artifacts if not any(_is_sha256(part) for part in str(item).lower().split(":"))]
    if unbound:
        raise ValueError("Every Build input must include a server-computed SHA-256 content hash.")
    return _sha256(sorted(dict.fromkeys(str(item) for item in input_artifacts)))


def _server_owned_input_provenance(checks: list[ValidityCheck]) -> list[ValidityCheck]:
    """Replace the input-provenance check with the server's own verdict.

    A human may assess scientific evidence, but cannot truthfully declare that
    the server did not bind inputs when the durable Build-lineage writer did.
    The assessment path separately refuses a cycle with no lineage, so a pass
    here is conditional on an exact lineage row being bound later in the same
    transaction.
    """
    authoritative = ValidityCheck(
        check=ValidityCheckName.RECONCILED_INPUTS,
        status=CheckStatus.PASSED,
        detail=("The Build lineage binds every examined input to a server-computed content hash; Data Reconciliation is not a Build prerequisite."),
        evidence_refs=("server://dbtl/build-lineage",),
    )
    replaced = False
    normalized: list[ValidityCheck] = []
    for check in checks:
        if check.check is ValidityCheckName.RECONCILED_INPUTS:
            normalized.append(authoritative)
            replaced = True
        else:
            normalized.append(check)
    if not replaced:
        normalized.append(authoritative)
    return normalized


class BuildTestOpsMixin:
    """Reproducibility capture and human-owned validity routing."""

    @staticmethod
    def _lineage_payload(row: DbtlBuildLineageRow) -> dict[str, Any]:
        from deerflow.persistence.dbtl.cycles import _iso

        return {
            "id": row.id,
            "stage_attempt_id": row.stage_attempt_id,
            "lineage_revision": row.lineage_revision,
            "stage_spec_key": row.stage_spec_key,
            "dataset_fingerprint": row.dataset_fingerprint,
            "code_revision": row.code_revision,
            "config_revision": row.config_revision,
            "environment": dict(row.environment or {}),
            "rerun_spec": dict(row.rerun_spec or {}),
            "rerun_status": "verified" if row.rerun_spec else "rerun_unverified",
            "input_artifacts": list(row.input_artifacts or []),
            "output_artifacts": list(row.output_artifacts or []),
            "deviations": list(row.deviations or []),
            "logs_uri": row.logs_uri,
            "recorded_by": row.recorded_by,
            "db_revision": row.db_revision,
            "created_at": _iso(row.created_at),
        }

    @staticmethod
    def _assessment_payload(row: DbtlValidityAssessmentRow) -> dict[str, Any]:
        from deerflow.persistence.dbtl.cycles import _iso

        return {
            "id": row.id,
            "test_stage_attempt_id": row.test_stage_attempt_id,
            "build_lineage_id": row.build_lineage_id,
            "evidence_exception_artifact_id": row.evidence_exception_artifact_id,
            "evidence_exception_hash": row.evidence_exception_hash,
            "assessment_revision": row.assessment_revision,
            "validity_pack_key": row.validity_pack_key,
            "headline_metrics": list(row.headline_metrics or []),
            "checks": list(row.checks or []),
            "outcome": row.outcome,
            "recommendation": row.recommendation,
            "reason_codes": list(row.reason_codes or []),
            "limitations": list(row.limitations or []),
            "rationale": row.rationale,
            "reviewer_user_id": row.reviewer_user_id,
            "reviewer_project_role": row.reviewer_project_role,
            "db_revision": row.db_revision,
            "created_at": _iso(row.created_at),
        }

    async def _latest_build_lineage(
        self,
        session,
        cycle_id: str,
    ) -> DbtlBuildLineageRow | None:
        return await session.scalar(
            select(DbtlBuildLineageRow)
            .where(DbtlBuildLineageRow.cycle_id == cycle_id)
            .order_by(
                DbtlBuildLineageRow.lineage_revision.desc(),
                DbtlBuildLineageRow.created_at.desc(),
            )
            .limit(1)
        )

    async def _latest_validity_assessment(
        self,
        session,
        cycle_id: str,
    ) -> DbtlValidityAssessmentRow | None:
        return await session.scalar(
            select(DbtlValidityAssessmentRow)
            .where(DbtlValidityAssessmentRow.cycle_id == cycle_id)
            .order_by(
                DbtlValidityAssessmentRow.assessment_revision.desc(),
                DbtlValidityAssessmentRow.created_at.desc(),
            )
            .limit(1)
        )

    async def record_build_lineage(
        self,
        *,
        cycle_id: str,
        project_id: str,
        code_revision: str,
        config_revision: str,
        environment: dict[str, Any],
        input_artifacts: list[str],
        output_artifacts: list[dict[str, Any]],
        deviations: list[str],
        logs_uri: str,
        recorded_by: str,
        expected_db_revision: int,
        idempotency_key: str,
        rerun_spec: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a versioned, dataset-bound Build reproducibility package."""
        from deerflow.persistence.dbtl.cycles import DbtlWorkflowRefused

        if not code_revision.strip() or not config_revision.strip():
            raise ValueError("Build lineage requires code and configuration revisions.")
        if not isinstance(environment, dict) or not environment:
            raise ValueError("Build lineage requires a non-empty environment capture.")
        if not output_artifacts:
            raise ValueError("Build lineage requires at least one versioned output artifact.")
        for item in output_artifacts:
            if not isinstance(item, dict) or not str(item.get("uri") or "").strip() or not _is_sha256(item.get("content_hash")) or not isinstance(item.get("revision"), int) or int(item["revision"]) < 1:
                raise ValueError("Each Build output needs a URI, lowercase SHA-256 hash, and positive revision.")
        parsed_rerun = parse_rerun_spec(rerun_spec) if rerun_spec is not None else None
        if rerun_spec is not None and parsed_rerun is None:
            raise ValueError("Build lineage received an invalid structured rerun record.")
        # A generative Build (e.g. a seed-based simulation) legitimately examines no
        # external input file: its provenance is the reproducible rerun record plus the
        # hashed, immutable outputs below. Permit zero inputs ONLY when such a rerun
        # record is present; an empirical Build that declares neither inputs nor a rerun
        # record is still refused, so the input-provenance guarantee is unchanged for it.
        if not input_artifacts and parsed_rerun is None:
            raise ValueError("Build lineage requires at least one input file examined during Build, or a reproducible rerun record for a generative build.")
        canonical_rerun = parsed_rerun.as_dict() if parsed_rerun is not None else {}
        lineage_input = {
            "code_revision": code_revision.strip(),
            "config_revision": config_revision.strip(),
            "environment": environment,
            "rerun_spec": canonical_rerun,
            "input_artifacts": input_artifacts,
            "output_artifacts": output_artifacts,
            "deviations": deviations,
            "logs_uri": logs_uri.strip(),
        }
        digest = _sha256(lineage_input)

        async with self._sf() as session:
            loaded = await self._load(
                session,
                cycle_id,
                project_id,
                for_update=True,
            )
            if loaded is None:
                raise DbtlWorkflowRefused("Cycle not found.")
            cycle, stages = loaded
            replay = await self._replay_event(
                session,
                cycle_id,
                idempotency_key,
                event_type="build.lineage_recorded",
                expected_payload={
                    "lineage_digest": digest,
                    "expected_db_revision": expected_db_revision,
                    "recorded_by": recorded_by,
                },
            )
            if replay is not None:
                lineage_id = dict(replay.payload or {}).get("lineage_id")
                row = await session.get(DbtlBuildLineageRow, lineage_id)
                if row is None:
                    raise DbtlWorkflowRefused("The replayed Build lineage is no longer available.")
                return self._lineage_payload(row)

            self._require_revision(cycle, expected_db_revision)
            attempts = {item.stage: item for item in stages}
            build = attempts["build"]
            stage_spec_key = build.stage_spec_key or resolve_stage_spec("build").spec_key
            pinned_spec = resolve_spec_by_key(stage_spec_key)
            if "structured_rerun_spec" in pinned_spec.validity_gates and parsed_rerun is None:
                raise ValueError(f"Build lineage for {stage_spec_key} requires a valid structured rerun record.")
            if cycle.state not in {"ready_for_build", "build"}:
                raise DbtlWorkflowRefused("Build lineage can only be recorded after data readiness.")
            if build.status not in {
                StageStatus.IN_PROGRESS.value,
                StageStatus.CHANGES_REQUESTED.value,
            }:
                raise DbtlWorkflowRefused(f"Build lineage cannot be recorded while Build is {build.status!r}.")
            # Build owns input discovery. Workers name what they read and the
            # server computes those files' hashes; Test later checks leakage,
            # splits, and whether execution stayed bound to this lineage.
            fingerprint = _build_input_fingerprint(input_artifacts)

            highest = await session.scalar(select(func.max(DbtlBuildLineageRow.lineage_revision)).where(DbtlBuildLineageRow.stage_attempt_id == build.id))
            revision = int(highest or 0) + 1
            row = DbtlBuildLineageRow(
                id=f"build-lineage-{uuid4()}",
                project_id=project_id,
                cycle_id=cycle_id,
                stage_attempt_id=build.id,
                lineage_revision=revision,
                stage_spec_key=stage_spec_key,
                dataset_fingerprint=fingerprint,
                code_revision=code_revision.strip(),
                config_revision=config_revision.strip(),
                environment=dict(environment),
                rerun_spec=canonical_rerun,
                input_artifacts=list(input_artifacts),
                output_artifacts=list(output_artifacts),
                deviations=list(deviations),
                logs_uri=logs_uri.strip(),
                recorded_by=recorded_by,
                db_revision=cycle.db_revision + 1,
            )
            session.add(row)
            build.stage_spec_key = row.stage_spec_key
            statuses = self._statuses(stages)
            self._commit_revision(cycle, statuses)
            build.db_revision = cycle.db_revision
            await self._record_event(
                session,
                cycle=cycle,
                event_type="build.lineage_recorded",
                actor_user_id=recorded_by,
                payload={
                    "idempotency_key": idempotency_key,
                    "lineage_id": row.id,
                    "lineage_revision": revision,
                    "lineage_digest": digest,
                    "dataset_fingerprint": fingerprint,
                    "expected_db_revision": expected_db_revision,
                    "recorded_by": recorded_by,
                },
            )
            await session.commit()
            await session.refresh(row)
            return self._lineage_payload(row)

    async def build_test_view(
        self,
        cycle_id: str,
        *,
        project_id: str,
    ) -> dict[str, Any]:
        async with self._sf() as session:
            loaded = await self._load(session, cycle_id, project_id)
            if loaded is None:
                return {}
            cycle, _stages = loaded
            lineage = await self._latest_build_lineage(session, cycle_id)
            assessment = await self._latest_validity_assessment(
                session,
                cycle_id,
            )
            return {
                "cycle_id": cycle_id,
                "db_revision": cycle.db_revision,
                "validity_pack": {
                    "pack_key": DEFAULT_VALIDITY_PACK.pack_key,
                    "title": DEFAULT_VALIDITY_PACK.title,
                    "provisional": DEFAULT_VALIDITY_PACK.provisional,
                    "required_checks": [item.value for item in DEFAULT_VALIDITY_PACK.required_checks],
                },
                "build_lineage": (self._lineage_payload(lineage) if lineage else None),
                "validity_assessment": (self._assessment_payload(assessment) if assessment else None),
            }

    async def record_validity_assessment(
        self,
        *,
        cycle_id: str,
        project_id: str,
        metrics: list[dict[str, Any]],
        checks: list[dict[str, Any]],
        recommendation: str,
        limitations: list[str],
        rationale: str,
        reviewer_user_id: str,
        reviewer_project_role: str,
        expected_db_revision: int,
        idempotency_key: str,
        review_provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compute, record, and route one human Test assessment."""
        from deerflow.persistence.dbtl.cycles import DbtlWorkflowRefused

        if not rationale.strip():
            raise ValueError("A validity assessment requires a rationale.")
        metric_fields = {"name", "value", "threshold", "criterion", "plausible_max", "unit"}
        parsed_metrics = [HeadlineMetric(**{key: value for key, value in item.items() if key in metric_fields}) for item in metrics]
        parsed_checks = [
            ValidityCheck(
                check=item["check"],
                status=item["status"],
                detail=str(item.get("detail") or ""),
                evidence_refs=tuple(item.get("evidence_refs") or ()),
            )
            for item in checks
        ]
        parsed_checks = _server_owned_input_provenance(parsed_checks)
        evaluation = evaluate_validity(
            metrics=parsed_metrics,
            checks=parsed_checks,
        )
        route = validate_recommendation(evaluation, recommendation)
        provenance = dict(review_provenance or {})
        request_digest = _sha256(
            {
                "metrics": [item.as_dict() for item in parsed_metrics],
                "checks": [item.as_dict() for item in parsed_checks],
                "recommendation": route.value,
                "limitations": limitations,
                "rationale": rationale.strip(),
                "review_provenance": provenance,
            }
        )

        async with self._sf() as session:
            loaded = await self._load(
                session,
                cycle_id,
                project_id,
                for_update=True,
            )
            if loaded is None:
                raise DbtlWorkflowRefused("Cycle not found.")
            cycle, stages = loaded
            replay = await self._replay_event(
                session,
                cycle_id,
                idempotency_key,
                event_type="test.validity_assessed",
                expected_payload={
                    "request_digest": request_digest,
                    "expected_db_revision": expected_db_revision,
                    "reviewer_user_id": reviewer_user_id,
                },
            )
            if replay is not None:
                assessment_id = dict(replay.payload or {}).get("assessment_id")
                assessment = await session.get(
                    DbtlValidityAssessmentRow,
                    assessment_id,
                )
                if assessment is None:
                    raise DbtlWorkflowRefused("The replayed validity assessment is no longer available.")
                return {
                    "cycle": self._cycle_payload(cycle, stages),
                    "validity_assessment": self._assessment_payload(assessment),
                }

            self._require_revision(cycle, expected_db_revision)
            attempts = {item.stage: item for item in stages}
            test = attempts["test"]
            if cycle.state == "build" and attempts["build"].status == StageStatus.APPROVED.value and test.status == StageStatus.AWAITING_REVIEW.value:
                # Repair the same bounded rollout shape projected by
                # ``_cycle_payload``. The assessment event below commits the
                # corrected cursor and the human decision in one revision.
                cycle.state = "test"
            if cycle.state != "test" or test.status != StageStatus.AWAITING_REVIEW.value:
                raise DbtlWorkflowRefused("Test must be awaiting review before validity can be assessed.")
            # A review meeting annotates the validity pack; it never replaces
            # it.  Bind the human-owned outcome to Test's core evidence even
            # when a newer ``test_review_meeting`` artifact sits beside it.
            evidence = await session.scalar(
                select(DbtlArtifactRow)
                .where(
                    DbtlArtifactRow.stage_attempt_id == test.id,
                    DbtlArtifactRow.artifact_type.in_({"validity_report", "test_report"}),
                )
                .order_by(
                    DbtlArtifactRow.revision.desc(),
                    DbtlArtifactRow.created_at.desc(),
                )
                .limit(1)
            )
            if evidence is None:
                evidence = await self._latest_reviewable_artifact(
                    session,
                    test.id,
                    "test",
                )
            if evidence is None:
                raise DbtlWorkflowRefused("Test has no evidence artifact to assess.")
            lineage = await self._latest_build_lineage(session, cycle_id)
            dossier = await session.scalar(
                select(DbtlArtifactRow)
                .where(
                    DbtlArtifactRow.stage_attempt_id == test.id,
                    DbtlArtifactRow.artifact_type == "evidence_exception",
                )
                .order_by(
                    DbtlArtifactRow.revision.desc(),
                    DbtlArtifactRow.created_at.desc(),
                )
                .limit(1)
            )
            exception_evidence = dossier
            if exception_evidence and not degraded_evidence_continuation_enabled():
                raise DbtlWorkflowRefused("Degraded-evidence continuation is disabled.")
            if lineage is None and (not exception_evidence or evaluation.outcome is not ValidityOutcome.INVALIDATED):
                raise DbtlWorkflowRefused("Test validity cannot be assessed without Build lineage.")

            # The stage graph is the route authority; a validity recommendation
            # may not authorize an edge the graph does not offer.
            self._require_graph_route(
                outcome=evaluation.outcome,
                route=route,
            )

            bound_projection_hash = cycle.projection_hash
            self._apply_validity_route(
                cycle,
                attempts,
                evaluation.outcome,
                route,
            )
            statuses = self._statuses(stages)
            self._commit_revision(cycle, statuses)
            for stage in stages:
                stage.db_revision = cycle.db_revision
            test.stage_spec_key = resolve_stage_spec("test").spec_key

            highest = await session.scalar(select(func.max(DbtlValidityAssessmentRow.assessment_revision)).where(DbtlValidityAssessmentRow.test_stage_attempt_id == test.id))
            assessment_limitations = list(dict.fromkeys(item.strip() for item in limitations if item.strip()))
            if exception_evidence is not None:
                effect = "invalidates scientific support" if evaluation.outcome is ValidityOutcome.INVALIDATED else "limits the scope of any supported claim"
                assessment_limitations.append(f"Evidence exception {exception_evidence.content_hash} {effect}.")
            assessment = DbtlValidityAssessmentRow(
                id=f"validity-{uuid4()}",
                project_id=project_id,
                cycle_id=cycle_id,
                test_stage_attempt_id=test.id,
                build_lineage_id=(lineage.id if lineage is not None else None),
                evidence_exception_artifact_id=(exception_evidence.id if exception_evidence else None),
                evidence_exception_hash=(exception_evidence.content_hash if exception_evidence else None),
                assessment_revision=int(highest or 0) + 1,
                validity_pack_key=evaluation.validity_pack_key,
                headline_metrics=[item.as_dict() for item in parsed_metrics],
                checks=[item.as_dict() for item in parsed_checks],
                outcome=evaluation.outcome.value,
                recommendation=route.value,
                reason_codes=list(evaluation.reason_codes),
                limitations=list(dict.fromkeys(assessment_limitations)),
                rationale=rationale.strip(),
                reviewer_user_id=reviewer_user_id,
                reviewer_project_role=reviewer_project_role,
                db_revision=cycle.db_revision,
            )
            session.add(assessment)
            session.add(
                DbtlReviewRow(
                    id=f"review-{cycle.id}-{cycle.db_revision}",
                    project_id=project_id,
                    cycle_id=cycle_id,
                    stage_attempt_id=test.id,
                    artifact_id=evidence.id,
                    artifact_revision=evidence.revision,
                    bound_db_revision=expected_db_revision,
                    bound_stage_revision=expected_db_revision,
                    bound_projection_hash=bound_projection_hash,
                    policy_version=cycle.policy_version,
                    idempotency_key=idempotency_key,
                    decision=self._review_decision_for_route(
                        evaluation.outcome,
                        route,
                    ),
                    rationale=rationale.strip(),
                    reviewer_user_id=reviewer_user_id,
                    reviewer_project_role=reviewer_project_role,
                    authorization_reference=(f"manual-validity:{project_id}:{reviewer_user_id}"),
                    input_source=str(provenance.get("input_source") or "test_chat"),
                    feedback_surface_id=provenance.get("feedback_surface_id"),
                    deck_content_hash=provenance.get("deck_content_hash"),
                    deck_schema_version=provenance.get("deck_schema_version"),
                    selected_action=provenance.get("selected_action"),
                    human_comment=provenance.get("human_comment"),
                    rationale_projection=provenance.get("rationale_projection"),
                    rationale_source=provenance.get("rationale_source"),
                )
            )
            await self._append_stage_transition(
                session,
                cycle=cycle,
                from_stage="test",
                chosen_route=route.value,
                decided_by=reviewer_user_id,
                stage_attempt=test,
                evidence_hash=evidence.content_hash,
                decision_surface_id=provenance.get("feedback_surface_id"),
                assessed_difficulty=("exception" if exception_evidence else None),
                assessment_rationale=(rationale.strip() if exception_evidence else None),
                offered_routes=([item.value for item in evaluation.allowed_recommendations] if exception_evidence else None),
            )
            await self._record_event(
                session,
                cycle=cycle,
                event_type="test.validity_assessed",
                actor_user_id=reviewer_user_id,
                payload={
                    "idempotency_key": idempotency_key,
                    "assessment_id": assessment.id,
                    "request_digest": request_digest,
                    "validity_pack_key": evaluation.validity_pack_key,
                    "outcome": evaluation.outcome.value,
                    "reason_codes": list(evaluation.reason_codes),
                    "recommendation": route.value,
                    "expected_db_revision": expected_db_revision,
                    "reviewer_user_id": reviewer_user_id,
                    "reviewer_project_role": reviewer_project_role,
                    "state": cycle.state,
                },
            )
            await session.commit()
            await session.refresh(assessment)
            return {
                "cycle": self._cycle_payload(cycle, stages),
                "validity_assessment": self._assessment_payload(assessment),
            }

    @staticmethod
    def _require_graph_route(
        *,
        outcome: ValidityOutcome,
        route: WorkflowRecommendation,
    ) -> None:
        """Refuse recommendations that are not legal graph edges."""
        routes = compute_stage_routes(
            RouteContext(
                stage="test",
                outcome=outcome.value,
            )
        )
        route_slug = {
            WorkflowRecommendation.ADVANCE_TO_LEARN: RouteSlug.ADVANCE,
            WorkflowRecommendation.LEARN_FROM_INVALIDATED_EVIDENCE: RouteSlug.LEARN_FROM_INVALIDATED_EVIDENCE,
            WorkflowRecommendation.REPEAT_TEST: RouteSlug.REVISE_HERE,
            WorkflowRecommendation.RETURN_TO_BUILD: RouteSlug.RETURN_TO_BUILD,
            WorkflowRecommendation.RETURN_TO_DESIGN: RouteSlug.RETURN_TO_DESIGN,
            WorkflowRecommendation.CLOSE_CYCLE: RouteSlug.CLOSE_CYCLE,
        }.get(route)
        selected = next(
            (candidate for candidate in routes if candidate.slug == route_slug),
            None,
        )
        if selected is None:
            raise ValidityRefused(f"Recommendation {route.value!r} is not a legal route from {outcome.value!r} Test evidence.")

    @staticmethod
    def _review_decision_for_route(
        outcome: ValidityOutcome,
        route: WorkflowRecommendation,
    ) -> str:
        if route is WorkflowRecommendation.ADVANCE_TO_LEARN:
            return "approve"
        if route is WorkflowRecommendation.LEARN_FROM_INVALIDATED_EVIDENCE:
            return "advanced_with_exception"
        if route is WorkflowRecommendation.CLOSE_CYCLE:
            return "approve" if outcome in {ValidityOutcome.SUPPORTED, ValidityOutcome.NOT_SUPPORTED} else "reject"
        return "request_changes"

    @staticmethod
    def _apply_validity_route(
        cycle,
        attempts: dict[str, DbtlStageAttemptRow],
        outcome: ValidityOutcome,
        route: WorkflowRecommendation,
    ) -> None:
        if route is WorkflowRecommendation.ADVANCE_TO_LEARN:
            attempts["test"].status = StageStatus.APPROVED.value
            attempts["learn"].status = StageStatus.IN_PROGRESS.value
            cycle.state = "learn"
            return
        if route is WorkflowRecommendation.LEARN_FROM_INVALIDATED_EVIDENCE:
            if outcome is not ValidityOutcome.INVALIDATED:
                raise AssertionError("Only invalidated Test evidence may take the exception Learn route.")
            attempts["test"].status = StageStatus.ADVANCED_WITH_EXCEPTION.value
            attempts["learn"].status = StageStatus.IN_PROGRESS.value
            cycle.state = "learn"
            return
        if route is WorkflowRecommendation.REPEAT_TEST:
            attempts["test"].status = StageStatus.CHANGES_REQUESTED.value
            cycle.state = "test"
            return
        if route is WorkflowRecommendation.RETURN_TO_BUILD:
            attempts["build"].status = StageStatus.CHANGES_REQUESTED.value
            attempts["test"].status = StageStatus.LOCKED.value
            attempts["learn"].status = StageStatus.LOCKED.value
            cycle.state = "build"
            return
        if route is WorkflowRecommendation.RETURN_TO_DESIGN:
            attempts["design"].status = StageStatus.CHANGES_REQUESTED.value
            for stage in ("reconciliation", "build", "test", "learn"):
                attempts[stage].status = StageStatus.LOCKED.value
            cycle.state = "design"
            return
        if route is WorkflowRecommendation.CLOSE_CYCLE:
            attempts["test"].status = StageStatus.APPROVED.value if outcome in {ValidityOutcome.SUPPORTED, ValidityOutcome.NOT_SUPPORTED} else StageStatus.REJECTED.value
            cycle.state = "completed"
            return
        raise AssertionError(f"Unhandled validity route {route!r}.")
