"""Durable DBTL cycle workflow API (Phase 3).

Reads are always available so the project rail can show honest state — "no
cycles yet", or an existing record — regardless of mode. **Mutations** are
gated on ``dbtl.mode`` reaching ``manual``: Phase 3 turns the workflow on, and
until an operator does that, a cycle cannot be created or advanced.

Every mutation carries the caller's expected revision and an idempotency key,
so a double-click, a retry, and a second reviewer on the same screen each
resolve to one durable outcome rather than three.
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.gateway.authz import require_permission
from app.gateway.deps import get_config, get_dbtl_cycle_repo, get_workspace_repo
from app.gateway.internal_auth import INTERNAL_SYSTEM_ROLE
from deerflow.config.app_config import AppConfig
from deerflow.dbtl import STAGE_ORDER
from deerflow.persistence.dbtl import (
    DbtlRevisionConflict,
    DbtlTopLevelCycleExists,
    DbtlWorkflowRefused,
)

router = APIRouter(prefix="/api", tags=["dbtl-cycles"])
logger = logging.getLogger(__name__)

StageName = Literal["design", "reconciliation", "build", "test", "learn"]
CycleWeight = Literal["full", "light", "retroactive"]


class CycleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    cycle_class: Literal["season/program", "computational", "other"]
    cycle_weight: CycleWeight = "full"
    research_question: str = Field(min_length=1, max_length=4000)
    objective: str = Field(default="", max_length=4000)
    success_criteria: str = Field(default="", max_length=4000)
    parent_cycle_id: str | None = Field(default=None, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("title", "research_question")
    @classmethod
    def must_have_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain text")
        return value.strip()


class StageSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_db_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)


class StageReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "request_changes", "reject"]
    rationale: str = Field(min_length=1, max_length=10_000)
    expected_db_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("rationale")
    @classmethod
    def rationale_must_have_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A review decision requires a rationale.")
        return value.strip()


class ArtifactCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: StageName
    artifact_type: str = Field(min_length=1, max_length=64)
    uri: str = Field(min_length=1, max_length=2000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_db_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("artifact_type", "uri")
    @classmethod
    def artifact_text_must_have_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain text")
        return value.strip()


class WorkItemCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    kind: Literal["blocker", "task", "question"]
    owner_role: str | None = Field(default=None, max_length=64)
    expected_db_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("title")
    @classmethod
    def title_must_have_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain text")
        return value.strip()


class WorkItemResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: str = Field(min_length=1, max_length=4000)
    expected_db_revision: int = Field(ge=1)
    expected_work_item_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("resolution")
    @classmethod
    def resolution_must_have_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain text")
        return value.strip()


async def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return str(user.id)


async def _require_project(project_id: str, request: Request) -> tuple[dict, str]:
    user_id = await _user_id(request)
    project = await get_workspace_repo(request).get_project(project_id, user_id=user_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project, user_id


def _require_human_reviewer(request: Request) -> None:
    user = getattr(request.state, "user", None)
    if getattr(user, "system_role", None) == INTERNAL_SYSTEM_ROLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="DBTL reviews require an authenticated human project member.",
        )


def _require_mutations_enabled(request: Request, config: AppConfig) -> None:
    """Fail closed until an operator turns the manual workflow on."""
    dbtl_config = getattr(request.app.state, "dbtl_config_override", config.dbtl)
    if not dbtl_config.mutations_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"DBTL workflow mutations are disabled in mode {dbtl_config.mode!r}; set dbtl.mode=manual to create or advance cycles."),
        )


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, DbtlTopLevelCycleExists):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This project already has an active cycle. Complete or abandon it first.")
    if isinstance(exc, DbtlRevisionConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This cycle changed since you loaded it. Reload and try again.")
    if isinstance(exc, DbtlWorkflowRefused):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    logger.exception("Unexpected DBTL workflow failure", exc_info=exc)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="The DBTL workflow operation failed.",
    )


# ── Reads ────────────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/dbtl/cycles")
@require_permission("threads", "read")
async def list_cycles(project_id: str, request: Request, repo=Depends(get_dbtl_cycle_repo)):
    await _require_project(project_id, request)
    return {"project_id": project_id, "stages": list(STAGE_ORDER), "cycles": await repo.list_cycles(project_id)}


@router.get("/projects/{project_id}/dbtl/cycles/{cycle_id}")
@require_permission("threads", "read")
async def get_cycle(project_id: str, cycle_id: str, request: Request, repo=Depends(get_dbtl_cycle_repo)):
    await _require_project(project_id, request)
    cycle = await repo.get_cycle(cycle_id, project_id=project_id)
    if cycle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")
    return cycle


@router.get("/projects/{project_id}/dbtl/cycles/{cycle_id}/activity")
@require_permission("threads", "read")
async def list_activity(project_id: str, cycle_id: str, request: Request, repo=Depends(get_dbtl_cycle_repo)):
    await _require_project(project_id, request)
    return {"cycle_id": cycle_id, "events": await repo.list_activity(cycle_id, project_id=project_id)}


# ── Mutations ────────────────────────────────────────────────────────────


@router.post("/projects/{project_id}/dbtl/cycles", status_code=status.HTTP_201_CREATED)
@require_permission("threads", "write")
async def create_cycle(
    project_id: str,
    body: CycleCreateRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    """Open a durable research record."""
    _project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    try:
        return await repo.create_cycle(
            cycle_id=f"cycle-{uuid4()}",
            project_id=project_id,
            title=body.title,
            cycle_class=body.cycle_class,
            cycle_weight=body.cycle_weight,
            research_question=body.research_question,
            objective=body.objective,
            success_criteria=body.success_criteria,
            created_by=user_id,
            policy_version=getattr(request.app.state, "dbtl_config_override", config.dbtl).policy_version,
            idempotency_key=body.idempotency_key,
            parent_cycle_id=body.parent_cycle_id,
        )
    except Exception as exc:  # noqa: BLE001 - translated to typed HTTP errors
        raise _translate(exc) from exc


@router.post("/projects/{project_id}/dbtl/cycles/{cycle_id}/stages/{stage}/submit")
@require_permission("threads", "write")
async def submit_stage(
    project_id: str,
    cycle_id: str,
    stage: StageName,
    body: StageSubmitRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    _project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    try:
        return await repo.submit_stage_for_review(
            cycle_id=cycle_id,
            project_id=project_id,
            stage=stage,
            expected_db_revision=body.expected_db_revision,
            actor_user_id=user_id,
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.post("/projects/{project_id}/dbtl/cycles/{cycle_id}/stages/{stage}/review")
@require_permission("threads", "write")
async def review_stage(
    project_id: str,
    cycle_id: str,
    stage: StageName,
    body: StageReviewRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    """Record one human verdict. Reviewer identity is server-owned."""
    project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    _require_human_reviewer(request)
    try:
        return await repo.review_stage(
            cycle_id=cycle_id,
            project_id=project_id,
            stage=stage,
            decision=body.decision,
            rationale=body.rationale,
            expected_db_revision=body.expected_db_revision,
            reviewer_user_id=user_id,
            reviewer_project_role=str(project["current_user_role"]),
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.post("/projects/{project_id}/dbtl/cycles/{cycle_id}/artifacts", status_code=status.HTTP_201_CREATED)
@require_permission("threads", "write")
async def attach_artifact(
    project_id: str,
    cycle_id: str,
    body: ArtifactCreateRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    _project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    try:
        return await repo.attach_artifact(
            cycle_id=cycle_id,
            project_id=project_id,
            stage=body.stage,
            artifact_type=body.artifact_type,
            uri=body.uri,
            content_hash=body.content_hash,
            created_by=user_id,
            expected_db_revision=body.expected_db_revision,
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.post("/projects/{project_id}/dbtl/cycles/{cycle_id}/work-items", status_code=status.HTTP_201_CREATED)
@require_permission("threads", "write")
async def create_work_item(
    project_id: str,
    cycle_id: str,
    body: WorkItemCreateRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    _project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    try:
        return await repo.create_work_item(
            cycle_id=cycle_id,
            project_id=project_id,
            title=body.title,
            kind=body.kind,
            created_by=user_id,
            owner_role=body.owner_role,
            expected_db_revision=body.expected_db_revision,
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.post("/projects/{project_id}/dbtl/work-items/{work_item_id}/resolve")
@require_permission("threads", "write")
async def resolve_work_item(
    project_id: str,
    work_item_id: str,
    body: WorkItemResolveRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    _project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    try:
        return await repo.resolve_work_item(
            work_item_id=work_item_id,
            project_id=project_id,
            resolution=body.resolution,
            actor_user_id=user_id,
            expected_db_revision=body.expected_db_revision,
            expected_work_item_revision=body.expected_work_item_revision,
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
