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

import hashlib
import logging
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.gateway.authz import require_permission
from app.gateway.deps import get_config, get_dbtl_cycle_repo, get_workspace_repo
from app.gateway.internal_auth import INTERNAL_SYSTEM_ROLE
from app.gateway.memory_scope_service import resolve_scope_bindings
from app.gateway.project_scope import ensure_project_root
from deerflow.agents.memory.scopes import bind_scope, publication_scope
from deerflow.config.app_config import AppConfig
from deerflow.dbtl import (
    STAGE_ORDER,
    ClaimGrade,
    KnowledgeLifecycleRefused,
    render_claim_markdown,
    validate_candidate_grade,
)
from deerflow.persistence.dbtl import (
    DbtlRevisionConflict,
    DbtlWorkflowRefused,
)
from deerflow.utils.file_io import run_file_io

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


class CycleAbandonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(min_length=1, max_length=4000)
    expected_db_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("rationale")
    @classmethod
    def rationale_must_have_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Removing a cycle requires a rationale.")
        return value.strip()


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
    if isinstance(exc, DbtlRevisionConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This cycle changed since you loaded it. Reload and try again.")
    if isinstance(exc, DbtlWorkflowRefused):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, KnowledgeLifecycleRefused):
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


@router.get("/projects/{project_id}/dbtl/cycles/{cycle_id}/design-feedback/{surface_id}")
@require_permission("threads", "read")
async def get_design_feedback_surface(
    project_id: str,
    cycle_id: str,
    surface_id: str,
    request: Request,
    repo=Depends(get_dbtl_cycle_repo),
):
    """Resolve a rendered Design deck to what the server knows about it.

    This is what a parent application asks before treating any HTML as a Design
    surface. It is deliberately a **read**: it reports what a deck is bound to
    and what may be done with it, and in this phase the answer is always
    "nothing" — the bridge and the in-deck transitions do not exist yet, so
    advertising an action would describe a capability that is not there.

    Available in every mode, including ``audit_only``. A read model that
    disappeared when mutations were off could not tell an owner *why* their deck
    is inert, which is the one thing they need to know in that state.
    """
    await _require_project(project_id, request)
    surface = await repo.get_design_feedback_surface(surface_id, project_id=project_id)
    # The cycle in the path is part of the addressing rule: a surface that
    # resolves under a different cycle is not this cycle's to serve.
    if surface is None or surface["cycle_id"] != cycle_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback surface not found")

    newest = await repo.latest_design_feedback_surface(project_id=project_id, cycle_id=cycle_id)
    return {
        **surface,
        # Where to go when this deck is no longer the live one. Reported even
        # for a current surface so a client never has to guess whether the
        # absence of this field means "current" or "unknown".
        "newest_surface_id": (newest or {}).get("surface_id"),
        # Phase 1 registers decks; it does not make them answerable. Both keys
        # are served rather than omitted so a client cannot read a missing key
        # as permission.
        "allowed_actions": [],
        "interactive": False,
    }


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


@router.post("/projects/{project_id}/dbtl/cycles/{cycle_id}/abandon")
@require_permission("threads", "write")
async def abandon_cycle(
    project_id: str,
    cycle_id: str,
    body: CycleAbandonRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    """Retire a live cycle while preserving its durable activity record."""
    _project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    _require_human_reviewer(request)
    try:
        return await repo.abandon_cycle(
            cycle_id=cycle_id,
            project_id=project_id,
            expected_db_revision=body.expected_db_revision,
            actor_user_id=user_id,
            idempotency_key=body.idempotency_key,
            rationale=body.rationale,
        )
    except Exception as exc:  # noqa: BLE001
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


# ── Phase 6: data readiness and reconciliation ───────────────────────────
#
# Reviewer identity stays server-owned here exactly as it is for stage reviews:
# these request models forbid extra fields, so a client-supplied actor is a 422
# rather than a trusted claim, and the decision endpoint records the
# authenticated caller. There is deliberately **no** way to submit an agent
# decision over HTTP — an agent's proposal reaches the matrix through the stage
# runner, and letting a browser assert `actor_type="agent"` would make the
# human-decision rule a matter of what the client chose to send.


class DatasetDeclareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(min_length=1, max_length=120)
    uri: str = Field(min_length=1, max_length=2000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    declared_immutable: bool = True
    role: Literal["raw", "derived", "reference"] = "raw"
    expected_db_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("source_key", "uri")
    @classmethod
    def dataset_text_must_have_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain text")
        return value.strip()


class ReconciliationRowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: str = Field(min_length=1, max_length=64)
    field_name: str = Field(min_length=1, max_length=240)
    source_a_label: str = Field(default="", max_length=120)
    source_a_value: str = Field(default="", max_length=1000)
    source_b_label: str = Field(default="", max_length=120)
    source_b_value: str = Field(default="", max_length=1000)
    required: bool = True
    expected_db_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("field_name")
    @classmethod
    def field_name_must_have_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain text")
        return value.strip()


class ReconciliationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # "proposed" is absent on purpose: a person's decision is a decision, and
    # the pure layer refuses a human proposal anyway.
    status: Literal["resolved", "blocked", "waived"]
    resolution: str = Field(min_length=1, max_length=4000)
    blocker_kind: Literal["missing_data", "conflicting_sources", "indeterminate"] | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)
    expected_db_revision: int = Field(ge=1)
    expected_work_item_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("resolution")
    @classmethod
    def resolution_must_have_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reconciliation decision requires a rationale.")
        return value.strip()

    @field_validator("evidence_refs")
    @classmethod
    def evidence_refs_must_be_bounded(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if any(len(item) > 1000 for item in cleaned):
            raise ValueError("Each evidence reference must be at most 1000 characters.")
        return list(dict.fromkeys(cleaned))


class BuildLineageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code_revision: str = Field(min_length=1, max_length=160)
    config_revision: str = Field(min_length=1, max_length=160)
    environment: dict[str, object]
    input_artifacts: list[str] = Field(min_length=1, max_length=100)
    output_artifacts: list[dict[str, object]] = Field(min_length=1, max_length=100)
    deviations: list[str] = Field(default_factory=list, max_length=100)
    logs_uri: str = Field(default="", max_length=2000)
    expected_db_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("code_revision", "config_revision")
    @classmethod
    def revision_must_have_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain text")
        return value.strip()


class HeadlineMetricRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    value: float
    threshold: float
    criterion: Literal["gte", "lte"] = "gte"
    plausible_max: float | None = None
    unit: str = Field(default="", max_length=40)


class ValidityCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: str = Field(min_length=1, max_length=64)
    status: Literal["passed", "failed", "missing", "not_applicable"]
    detail: str = Field(default="", max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)


class ValidityAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[HeadlineMetricRequest] = Field(max_length=100)
    checks: list[ValidityCheckRequest] = Field(max_length=100)
    recommendation: Literal[
        "advance_to_learn",
        "repeat_test",
        "return_to_build",
        "return_to_reconciliation",
        "return_to_design",
        "close_cycle",
    ]
    limitations: list[str] = Field(default_factory=list, max_length=100)
    rationale: str = Field(min_length=1, max_length=10_000)
    expected_db_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("rationale")
    @classmethod
    def validity_rationale_must_have_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A validity assessment requires a rationale.")
        return value.strip()


@router.get("/projects/{project_id}/dbtl/stage-specs")
@require_permission("threads", "read")
async def list_stage_specs(project_id: str, request: Request):
    """The versioned contracts a new stage attempt would run under."""
    from deerflow.dbtl.reconciliation import CHECK_LABELS
    from deerflow.dbtl.stage_spec import (
        EXECUTABLE_STAGES,
        describe_specs,
        resolve_stage_spec,
    )

    await _require_project(project_id, request)
    return {
        "project_id": project_id,
        "executable_stages": list(EXECUTABLE_STAGES),
        "specs": list(describe_specs(resolve_stage_spec(stage) for stage in EXECUTABLE_STAGES)),
        "reconciliation_checks": [{"id": key.value, "label": label} for key, label in CHECK_LABELS.items()],
    }


@router.get("/projects/{project_id}/dbtl/cycles/{cycle_id}/reconciliation")
@require_permission("threads", "read")
async def get_reconciliation(project_id: str, cycle_id: str, request: Request, repo=Depends(get_dbtl_cycle_repo)):
    """The matrix, the declared inputs, and the gate — in one read."""
    await _require_project(project_id, request)
    view = await repo.reconciliation_view(cycle_id, project_id=project_id)
    if not view:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")
    return view


@router.get("/projects/{project_id}/dbtl/cycles/{cycle_id}/build-test")
@require_permission("threads", "read")
async def get_build_test(
    project_id: str,
    cycle_id: str,
    request: Request,
    repo=Depends(get_dbtl_cycle_repo),
):
    """Build reproducibility and Test validity in one review projection."""
    await _require_project(project_id, request)
    view = await repo.build_test_view(cycle_id, project_id=project_id)
    if not view:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cycle not found",
        )
    return view


@router.get("/projects/{project_id}/dbtl/cycles/{cycle_id}/stages/{stage}/workers")
@require_permission("threads", "read")
async def list_stage_workers(project_id: str, cycle_id: str, stage: StageName, request: Request, repo=Depends(get_dbtl_cycle_repo)):
    await _require_project(project_id, request)
    return {"cycle_id": cycle_id, "stage": stage, "workers": await repo.list_worker_runs(cycle_id, project_id=project_id, stage=stage)}


@router.post("/projects/{project_id}/dbtl/cycles/{cycle_id}/datasets", status_code=status.HTTP_201_CREATED)
@require_permission("threads", "write")
async def declare_dataset(
    project_id: str,
    cycle_id: str,
    body: DatasetDeclareRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    _project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    try:
        return await repo.declare_dataset(
            cycle_id=cycle_id,
            project_id=project_id,
            source_key=body.source_key,
            uri=body.uri,
            content_hash=body.content_hash,
            recorded_by=user_id,
            declared_immutable=body.declared_immutable,
            role=body.role,
            expected_db_revision=body.expected_db_revision,
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.post("/projects/{project_id}/dbtl/cycles/{cycle_id}/reconciliation/rows", status_code=status.HTTP_201_CREATED)
@require_permission("threads", "write")
async def open_reconciliation_row(
    project_id: str,
    cycle_id: str,
    body: ReconciliationRowRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    _project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    try:
        return await repo.open_reconciliation_row(
            cycle_id=cycle_id,
            project_id=project_id,
            check=body.check,
            field_name=body.field_name,
            created_by=user_id,
            source_a_label=body.source_a_label,
            source_a_value=body.source_a_value,
            source_b_label=body.source_b_label,
            source_b_value=body.source_b_value,
            required=body.required,
            expected_db_revision=body.expected_db_revision,
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.post("/projects/{project_id}/dbtl/reconciliation/rows/{row_id}/decide")
@require_permission("threads", "write")
async def decide_reconciliation_row(
    project_id: str,
    row_id: str,
    body: ReconciliationDecisionRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    """Record a person's decision on one matrix row.

    Gated by ``_require_human_reviewer`` for the same reason stage review is:
    settling a contradiction is a review act, and the Gateway's own internal or
    scheduled principal must not be able to perform one even while carrying a
    member's identity.
    """
    _project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    _require_human_reviewer(request)
    try:
        return await repo.decide_reconciliation_row(
            row_id=row_id,
            project_id=project_id,
            status=body.status,
            resolution=body.resolution,
            actor_type="human",
            actor_user_id=user_id,
            blocker_kind=body.blocker_kind,
            evidence_refs=body.evidence_refs,
            expected_db_revision=body.expected_db_revision,
            expected_work_item_revision=body.expected_work_item_revision,
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


# ── Phase 7: Build lineage and Test validity ─────────────────────────────


@router.post(
    "/projects/{project_id}/dbtl/cycles/{cycle_id}/build/lineage",
    status_code=status.HTTP_201_CREATED,
)
@require_permission("threads", "write")
async def record_build_lineage(
    project_id: str,
    cycle_id: str,
    body: BuildLineageRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    _project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    try:
        return await repo.record_build_lineage(
            cycle_id=cycle_id,
            project_id=project_id,
            code_revision=body.code_revision,
            config_revision=body.config_revision,
            environment=body.environment,
            input_artifacts=body.input_artifacts,
            output_artifacts=body.output_artifacts,
            deviations=body.deviations,
            logs_uri=body.logs_uri,
            recorded_by=user_id,
            expected_db_revision=body.expected_db_revision,
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.post(
    "/projects/{project_id}/dbtl/cycles/{cycle_id}/test/assessment",
    status_code=status.HTTP_201_CREATED,
)
@require_permission("threads", "write")
async def record_validity_assessment(
    project_id: str,
    cycle_id: str,
    body: ValidityAssessmentRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    """Compute and route a Test outcome. Reviewer identity is server-owned."""
    project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    _require_human_reviewer(request)
    try:
        return await repo.record_validity_assessment(
            cycle_id=cycle_id,
            project_id=project_id,
            metrics=[item.model_dump() for item in body.metrics],
            checks=[item.model_dump() for item in body.checks],
            recommendation=body.recommendation,
            limitations=body.limitations,
            rationale=body.rationale,
            reviewer_user_id=user_id,
            reviewer_project_role=str(project["current_user_role"]),
            expected_db_revision=body.expected_db_revision,
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


# ── Phase 8: Learn and governed knowledge ───────────────────────────────


class CandidateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["keep", "discard"]
    rationale: str = Field(min_length=1, max_length=10_000)
    idempotency_key: str = Field(min_length=1, max_length=128)


class CandidatePromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=10_000)
    grade: Literal["supported", "valid_negative", "methodological", "qa_lesson"]
    limitations: list[str] = Field(default_factory=list, max_length=50)
    rationale: str = Field(min_length=1, max_length=10_000)
    supersedes_claim_id: str | None = Field(default=None, max_length=96)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ClaimPublicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_project_ids: list[str] = Field(min_length=1, max_length=50)
    rationale: str = Field(min_length=1, max_length=10_000)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ClaimRetractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(min_length=1, max_length=10_000)
    idempotency_key: str = Field(min_length=1, max_length=128)


def _write_text_projection(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _published_markdown(claim: dict, source_project_id: str, status_value: str) -> str:
    return "\n".join(
        [
            "# Published project knowledge",
            "",
            f"> SQL authority: `{claim['id']}` · source project: `{source_project_id}` · status: **{status_value}**",
            "",
            str(claim["statement"]),
            "",
            f"Grade: **{claim['grade']}**",
            "",
            "This is a selected-project publication pointer. The source claim and its audit history remain authoritative.",
            "",
        ]
    )


def _publication_fact_id(claim_id: str, target_project_id: str) -> str:
    digest = hashlib.sha256(f"{claim_id}:{target_project_id}".encode()).hexdigest()[:24]
    return f"knowledge-publication-{digest}"


async def _upsert_publication_pointer(
    request: Request,
    *,
    claim: dict,
    source_project_id: str,
    target_project_id: str,
) -> None:
    try:
        _root, store = resolve_scope_bindings(request)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_501_NOT_IMPLEMENTED:
            logger.info("Memory backend has no publication projection; SQL remains authoritative")
            return
        raise
    binding = bind_scope(publication_scope(target_project_id))
    await run_file_io(
        store.upsert_fact,
        {
            "id": _publication_fact_id(claim["id"], target_project_id),
            "content": (f"{claim['statement']}\nGrade: {claim['grade']}. Source claim: {claim['id']} in project {source_project_id}."),
            "category": "context",
            "confidence": 1.0,
            "knowledgePointer": {
                "claim_id": claim["id"],
                "source_project_id": source_project_id,
                "target_project_id": target_project_id,
                "status": "active",
            },
        },
        user_id=binding.user_id,
        agent_name=binding.agent_name,
    )


async def _remove_publication_pointer(request: Request, *, claim_id: str, target_project_id: str) -> None:
    try:
        _root, store = resolve_scope_bindings(request)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_501_NOT_IMPLEMENTED:
            return
        raise
    binding = bind_scope(publication_scope(target_project_id))
    await run_file_io(
        store.delete_fact,
        _publication_fact_id(claim_id, target_project_id),
        user_id=binding.user_id,
        agent_name=binding.agent_name,
    )


async def _withdraw_publication_retrieval(
    request: Request,
    *,
    claim: dict,
    claim_id: str,
    source_project_id: str,
    target_project_ids: list[str],
    workspace_repo,
    reviewer_user_id: str,
    status_value: str,
) -> list[str]:
    """Stop retrieval in every target, and never stop early.

    SQL is the authority and has already committed the retraction by the time
    this runs, so a target that raises must not abandon the targets after it:
    the loop that did would leave a withdrawn claim retrievable in every
    project it had not reached yet, indefinitely and with no repair path. Each
    target is therefore independent, and the ids that failed are returned so
    the caller can report stale retrieval rather than imply success.

    Removing the pointer is also deliberately not gated on the reviewer's
    membership of the *target* project. Only the human-readable projection
    needs that project's folder; retrieval is keyed by target id alone, and a
    reviewer who cannot see a target project must still be able to withdraw
    from it — otherwise a membership change silently pins the claim there.
    """
    failed: list[str] = []
    for target_project_id in target_project_ids:
        try:
            target = await workspace_repo.get_project(target_project_id, user_id=reviewer_user_id)
            if target is not None:
                target_root = await ensure_project_root(workspace_repo, target, request)
                if target_root:
                    await run_file_io(
                        _write_text_projection,
                        Path(target_root) / "knowledge" / "published" / f"{claim_id}.md",
                        _published_markdown(claim, source_project_id, status_value),
                    )
        except Exception:  # noqa: BLE001
            # The projection is a convenience copy; failing to update it must
            # not prevent the retrieval removal below, which is the part that
            # actually withdraws the claim.
            logger.exception("Failed to update retracted publication projection for %s in %s", claim_id, target_project_id)
        try:
            await _remove_publication_pointer(request, claim_id=claim_id, target_project_id=target_project_id)
        except Exception:  # noqa: BLE001
            failed.append(target_project_id)
            logger.exception("Failed to remove publication pointer for %s in %s; retrieval may be stale", claim_id, target_project_id)
    return failed


@router.get("/projects/{project_id}/dbtl/knowledge")
@require_permission("threads", "read")
async def get_project_knowledge(
    project_id: str,
    request: Request,
    cycle_id: str | None = None,
    repo=Depends(get_dbtl_cycle_repo),
):
    await _require_project(project_id, request)
    return await repo.knowledge_view(project_id, cycle_id=cycle_id)


@router.post("/projects/{project_id}/dbtl/candidates/{candidate_id}/decision")
@require_permission("threads", "write")
async def decide_knowledge_candidate(
    project_id: str,
    candidate_id: str,
    body: CandidateDecisionRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    _project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    _require_human_reviewer(request)
    try:
        return await repo.decide_candidate(
            candidate_id=candidate_id,
            project_id=project_id,
            decision=body.decision,
            actor_user_id=user_id,
            rationale=body.rationale,
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.post("/projects/{project_id}/dbtl/candidates/{candidate_id}/promote")
@require_permission("threads", "write")
async def promote_knowledge_candidate(
    project_id: str,
    candidate_id: str,
    body: CandidatePromotionRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    _require_human_reviewer(request)
    view = await repo.knowledge_view(project_id)
    candidate = next(
        (item for item in view["candidates"] if item["id"] == candidate_id),
        None,
    )
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
    try:
        grade = validate_candidate_grade(str(candidate.get("test_outcome") or ""), body.grade)
        claim_id = f"claim-{uuid4()}"
        rendered_uri = f"/mnt/user-data/workspace/knowledge/{claim_id}.md"
        result = await repo.promote_candidate(
            candidate_id=candidate_id,
            project_id=project_id,
            statement=body.statement,
            grade=grade.value,
            limitations=body.limitations,
            reviewer_user_id=user_id,
            reviewer_project_role=str(project["current_user_role"]),
            rationale=body.rationale,
            authorization_reference=f"manual-promotion:{project_id}:{user_id}",
            idempotency_key=body.idempotency_key,
            rendered_uri=rendered_uri,
            claim_id=claim_id,
            supersedes_claim_id=body.supersedes_claim_id,
        )
        claim = result["claim"]
        project_root = await ensure_project_root(get_workspace_repo(request), project, request)
        if project_root:
            markdown = render_claim_markdown(
                claim_id=claim["id"],
                statement=claim["statement"],
                grade=ClaimGrade(claim["grade"]),
                evidence=claim["evidence"],
                limitations=claim["limitations"],
                reviewer_user_id=user_id,
                status=claim["status"],
            )
            await run_file_io(
                _write_text_projection,
                Path(project_root) / "knowledge" / f"{claim['id']}.md",
                markdown,
            )
        if body.supersedes_claim_id:
            superseded = next(item for item in result["claims"] if item["id"] == body.supersedes_claim_id)
            if project_root:
                await run_file_io(
                    _write_text_projection,
                    Path(project_root) / "knowledge" / f"{superseded['id']}.md",
                    render_claim_markdown(
                        claim_id=superseded["id"],
                        statement=superseded["statement"],
                        grade=ClaimGrade(superseded["grade"]),
                        evidence=superseded["evidence"],
                        limitations=superseded["limitations"],
                        reviewer_user_id=user_id,
                        status="superseded",
                    ),
                )
            workspace_repo = get_workspace_repo(request)
            # Same contract as retraction: the superseded claim's retrieval is
            # withdrawn from every target independently, so one failing target
            # cannot leave the rest still serving an outdated claim.
            stale = await _withdraw_publication_retrieval(
                request,
                claim=superseded,
                claim_id=str(superseded["id"]),
                source_project_id=project_id,
                target_project_ids=[publication["target_project_id"] for publication in result["publications"] if publication["claim_id"] == superseded["id"]],
                workspace_repo=workspace_repo,
                reviewer_user_id=user_id,
                status_value="superseded",
            )
            if stale:
                return {**result, "stale_retrieval_project_ids": stale}
        return result
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.post("/projects/{project_id}/dbtl/claims/{claim_id}/publish")
@require_permission("threads", "write")
async def publish_knowledge_claim(
    project_id: str,
    claim_id: str,
    body: ClaimPublicationRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    _require_human_reviewer(request)
    try:
        result = await repo.publish_claim(
            claim_id=claim_id,
            source_project_id=project_id,
            target_project_ids=body.target_project_ids,
            publisher_user_id=user_id,
            publisher_project_role=str(project["current_user_role"]),
            rationale=body.rationale,
            authorization_reference=f"manual-publication:{project_id}:{user_id}",
            idempotency_key=body.idempotency_key,
        )
        claim = next(item for item in result["claims"] if item["id"] == claim_id)
        workspace_repo = get_workspace_repo(request)
        for publication in result["publications"]:
            if publication["claim_id"] != claim_id or publication["target_project_id"] not in body.target_project_ids:
                continue
            target = await workspace_repo.get_project(publication["target_project_id"], user_id=user_id)
            if target is None:
                continue
            target_root = await ensure_project_root(workspace_repo, target, request)
            if target_root:
                await run_file_io(
                    _write_text_projection,
                    Path(target_root) / "knowledge" / "published" / f"{claim_id}.md",
                    _published_markdown(claim, project_id, "active"),
                )
            await _upsert_publication_pointer(
                request,
                claim=claim,
                source_project_id=project_id,
                target_project_id=publication["target_project_id"],
            )
        return result
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.post("/projects/{project_id}/dbtl/claims/{claim_id}/retract")
@require_permission("threads", "write")
async def retract_knowledge_claim(
    project_id: str,
    claim_id: str,
    body: ClaimRetractionRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    _require_human_reviewer(request)
    try:
        result = await repo.retract_claim(
            claim_id=claim_id,
            project_id=project_id,
            reviewer_user_id=user_id,
            reviewer_project_role=str(project["current_user_role"]),
            rationale=body.rationale,
            authorization_reference=f"manual-retraction:{project_id}:{user_id}",
            idempotency_key=body.idempotency_key,
        )
        claim = result["claim"]
        workspace_repo = get_workspace_repo(request)
        source_root = await ensure_project_root(workspace_repo, project, request)
        if source_root:
            await run_file_io(
                _write_text_projection,
                Path(source_root) / "knowledge" / f"{claim_id}.md",
                render_claim_markdown(
                    claim_id=claim_id,
                    statement=claim["statement"],
                    grade=ClaimGrade(claim["grade"]),
                    evidence=claim["evidence"],
                    limitations=claim["limitations"],
                    reviewer_user_id=user_id,
                    status="retracted",
                ),
            )
        stale = await _withdraw_publication_retrieval(
            request,
            claim=claim,
            claim_id=claim_id,
            source_project_id=project_id,
            target_project_ids=[publication["target_project_id"] for publication in result["publications"] if publication["claim_id"] == claim_id],
            workspace_repo=workspace_repo,
            reviewer_user_id=user_id,
            status_value="retracted",
        )
        # The retraction itself is committed and authoritative. Report any
        # project whose retrieval could not be updated instead of returning a
        # clean result that would read as "withdrawn everywhere".
        return {**result, "stale_retrieval_project_ids": stale}
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
