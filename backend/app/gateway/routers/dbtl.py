"""DBTL audit readiness and Phase 1 governance endpoints."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.gateway.auth_disabled import AUTH_SOURCE_INTERNAL
from app.gateway.authz import require_permission
from app.gateway.dbtl_governance import build_governance_report
from app.gateway.dbtl_readiness import DbtlReadinessReport, scan_dbtl_readiness
from app.gateway.deps import (
    get_config,
    get_dbtl_governance_repo,
    get_workspace_repo,
    require_admin_user,
)
from deerflow.config.app_config import AppConfig
from deerflow.config.dbtl_config import DbtlConfig
from deerflow.persistence.dbtl import (
    DbtlCutoverBlocked,
    DbtlGovernanceRepository,
    DbtlProjectionMismatch,
    DbtlReviewReplay,
    DbtlReviewStale,
)
from deerflow.persistence.workspaces import WorkspaceRepository

router = APIRouter(tags=["dbtl"])


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject", "request_changes"]
    rationale: str = Field(min_length=1, max_length=10_000)

    @field_validator("rationale")
    @classmethod
    def rationale_must_have_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rationale must contain text")
        return value.strip()


class SubmitReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_attempt_id: str = Field(min_length=1, max_length=96)
    artifact_id: str = Field(min_length=1, max_length=96)
    artifact_revision: int = Field(ge=1)
    expected_db_revision: int = Field(ge=1)
    expected_stage_revision: int = Field(ge=1)
    expected_projection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: str = Field(min_length=1, max_length=96)
    idempotency_key: str = Field(min_length=1, max_length=128)
    review: ReviewDecision


class CutoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    validation_id: str = Field(min_length=1, max_length=96)


def _runtime_inputs(request: Request, config: AppConfig) -> tuple[Path, DbtlConfig, str]:
    root = Path(getattr(request.app.state, "dbtl_root_override", AppConfig.resolve_config_path().parent))
    dbtl_config = getattr(request.app.state, "dbtl_config_override", config.dbtl)
    database_backend = getattr(
        request.app.state,
        "dbtl_database_backend",
        config.database.backend,
    )
    return root, dbtl_config, database_backend


def _human_user_id(request: Request) -> str:
    if getattr(request.state, "auth_source", None) == AUTH_SOURCE_INTERNAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Internal and scheduled callers cannot consume a human review gate.",
        )
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return str(user.id)


@router.get("/api/dbtl/readiness", response_model=DbtlReadinessReport)
@require_permission("threads", "read")
async def get_dbtl_readiness(
    request: Request,
    config: AppConfig = Depends(get_config),
) -> DbtlReadinessReport:
    """Return an audit-only inventory rooted beside the active config file."""
    root, dbtl_config, _ = _runtime_inputs(request, config)
    return await asyncio.to_thread(scan_dbtl_readiness, root, dbtl_config)


@router.post(
    "/api/projects/{project_id}/dbtl/cycles/{cycle_id}/reviews",
    status_code=status.HTTP_201_CREATED,
)
@require_permission("threads", "write")
async def submit_dbtl_review(
    project_id: str,
    cycle_id: str,
    body: SubmitReviewRequest,
    request: Request,
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    governance_repo: DbtlGovernanceRepository = Depends(get_dbtl_governance_repo),
):
    """Consume one revision-bound review gate using server-owned identity."""
    reviewer_user_id = _human_user_id(request)
    project = await workspace_repo.get_project(project_id, user_id=reviewer_user_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DBTL cycle not found")
    if project["current_user_role"] not in {"owner", "admin", "member"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your project role cannot approve DBTL review gates.",
        )
    try:
        return await governance_repo.submit_review(
            review_id=f"review-{uuid4()}",
            project_id=project_id,
            cycle_id=cycle_id,
            stage_attempt_id=body.stage_attempt_id,
            artifact_id=body.artifact_id,
            artifact_revision=body.artifact_revision,
            expected_db_revision=body.expected_db_revision,
            expected_stage_revision=body.expected_stage_revision,
            expected_projection_hash=body.expected_projection_hash,
            policy_version=body.policy_version,
            idempotency_key=body.idempotency_key,
            decision=body.review.decision,
            rationale=body.review.rationale,
            reviewer_user_id=reviewer_user_id,
            reviewer_project_role=project["current_user_role"],
            authorization_reference=f"workspace-membership:{project['workspace_id']}:{reviewer_user_id}",
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DBTL cycle not found") from exc
    except DbtlReviewReplay as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This review was already submitted.") from exc
    except DbtlReviewStale as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This review targets a stale revision.") from exc
    except DbtlProjectionMismatch as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The DBTL projection does not match durable state; operator review is required.",
        ) from exc


async def _operator_report(
    request: Request,
    config: AppConfig,
    repository: DbtlGovernanceRepository,
) -> dict:
    await require_admin_user(request, detail="DBTL governance requires an administrator.")
    root, dbtl_config, database_backend = _runtime_inputs(request, config)
    report = await build_governance_report(
        repository=repository,
        database_backend=database_backend,
        root=root,
        config=dbtl_config,
        sandbox_provider=str(getattr(config.sandbox, "use", "") or ""),
        allow_host_bash=bool(getattr(config.sandbox, "allow_host_bash", False)),
    )
    latest_validation = await repository.latest_validation()
    latest_cutover = await repository.latest_cutover()
    report["last_validation"] = (
        {
            "id": latest_validation["id"],
            "created_at": latest_validation["created_at"],
            "evidence_hash": latest_validation["evidence_hash"],
            "technical_ready": latest_validation["technical_ready"],
        }
        if latest_validation
        else None
    )
    report["cutover"] = latest_cutover
    report["operator_can_approve"] = bool(report["technical_ready"] and database_backend == "postgres" and latest_cutover is None)
    report["checks"].append(
        {
            "id": "cutover-approval",
            "title": "Human cutover approval",
            "status": "passed" if latest_cutover else "waiting",
            "detail": (f"Approved by {latest_cutover['approved_by']}." if latest_cutover else "Waiting for an administrator after every technical check passes."),
        }
    )
    report["total_checks"] = len(report["checks"])
    report["passed_checks"] = sum(check["status"] == "passed" for check in report["checks"])
    return report


@router.get("/api/dbtl/governance/readiness")
@require_permission("threads", "read")
async def get_governance_readiness(
    request: Request,
    config: AppConfig = Depends(get_config),
    repository: DbtlGovernanceRepository = Depends(get_dbtl_governance_repo),
):
    return await _operator_report(request, config, repository)


@router.post("/api/dbtl/governance/validate")
@require_permission("threads", "write")
async def validate_governance(
    request: Request,
    config: AppConfig = Depends(get_config),
    repository: DbtlGovernanceRepository = Depends(get_dbtl_governance_repo),
):
    created_by = _human_user_id(request)
    report = await _operator_report(request, config, repository)
    validation = await repository.save_validation(
        validation_id=f"validation-{uuid4()}",
        database_backend=report["database_backend"],
        technical_ready=report["technical_ready"],
        report=report,
        created_by=created_by,
    )
    return {
        **report,
        "validation_id": validation["id"],
        "evidence_hash": validation["evidence_hash"],
        "validated_at": validation["created_at"],
        "last_validation": {
            "id": validation["id"],
            "created_at": validation["created_at"],
            "evidence_hash": validation["evidence_hash"],
            "technical_ready": validation["technical_ready"],
        },
    }


@router.post("/api/dbtl/governance/cutover")
@require_permission("threads", "write")
async def approve_governance_cutover(
    body: CutoverRequest,
    request: Request,
    repository: DbtlGovernanceRepository = Depends(get_dbtl_governance_repo),
):
    approved_by = _human_user_id(request)
    await require_admin_user(request, detail="DBTL cutover approval requires an administrator.")
    try:
        return await repository.approve_cutover(
            decision_id=f"cutover-{uuid4()}",
            validation_id=body.validation_id,
            approved_by=approved_by,
        )
    except DbtlCutoverBlocked as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cutover is blocked until a PostgreSQL validation passes every technical check.",
        ) from exc
