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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.gateway.authz import require_permission
from app.gateway.dbtl_round_watch import watch_background_round
from app.gateway.deps import (
    get_config,
    get_dbtl_cycle_repo,
    get_run_event_store,
    get_run_store,
    get_thread_store,
    get_workspace_repo,
)
from app.gateway.internal_auth import INTERNAL_SYSTEM_ROLE
from app.gateway.memory_scope_service import resolve_scope_bindings
from app.gateway.project_scope import ensure_project_root
from app.gateway.run_models import RunCreateRequest
from app.gateway.services import start_run
from deerflow.agents.dbtl.live_stage.test_review import TestReviewService
from deerflow.agents.memory.scopes import bind_scope, publication_scope
from deerflow.config.app_config import AppConfig
from deerflow.dbtl import (
    KNOWLEDGE_AUTHORITY_ROLES,
    STAGE_ORDER,
    ClaimGrade,
    KnowledgeLifecycleRefused,
    render_claim_markdown,
    validate_candidate_grade,
)
from deerflow.dbtl.policy import DBTL_POLICY_VERSION
from deerflow.dbtl.reconciliation_policy import conditional_test_enabled
from deerflow.dbtl.stage_feedback import filter_stage_feedback_intents, is_core_review_artifact
from deerflow.dbtl.stage_meetings import (
    TRANSITION_INTENTS,
    apply_meeting_gate,
    review_meeting_recorded,
    surface_meeting_gate,
)
from deerflow.persistence.dbtl import (
    DbtlRevisionConflict,
    DbtlWorkflowRefused,
    DesignFeedbackConflict,
)
from deerflow.utils.file_io import run_file_io
from deerflow.utils.thread_id import ThreadId

router = APIRouter(prefix="/api", tags=["dbtl-cycles"])
logger = logging.getLogger(__name__)

StageName = Literal["design", "reconciliation", "build", "test", "learn"]
CycleWeight = Literal["full", "light", "retroactive"]
_TRANSITION_DIFFICULTIES = frozenset({"routine", "standard", "high_stakes", "exception"})


def _reviewable_attempt_artifacts(stage: str, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    core = [item for item in artifacts if is_core_review_artifact(stage, item.get("artifact_type"))]
    if core:
        return core
    return [item for item in artifacts if item.get("artifact_type") == "evidence_exception"]


@dataclass(frozen=True, slots=True)
class _PostApprovalHandoff:
    """Outcome of delivering the already-committed approval into chat."""

    status: Literal["started", "failed", "not_needed"]
    next_stage: str | None = None
    run_id: str | None = None
    failure_code: str | None = None


def _surface_transition_gate(surface: dict[str, Any]) -> dict[str, Any] | None:
    request_payload = surface.get("decision_request")
    gate = request_payload.get("transition_gate") if isinstance(request_payload, dict) else None
    if not isinstance(gate, dict):
        return None
    assessment = gate.get("assessment")
    routes = gate.get("routes")
    if not isinstance(assessment, dict) or not isinstance(routes, list):
        return None
    difficulty = str(assessment.get("difficulty") or "")
    rationale = str(assessment.get("rationale") or "").strip()
    if difficulty not in _TRANSITION_DIFFICULTIES or not rationale:
        return None
    result = {
        "stage": str(gate.get("stage") or "design"),
        "assessment": {
            "difficulty": difficulty,
            "rationale": rationale,
            "source": str(assessment.get("source") or ""),
        },
        "routes": [dict(item) for item in routes if isinstance(item, dict)],
    }
    evidence_exception = gate.get("evidence_exception")
    if isinstance(evidence_exception, dict):
        result["evidence_exception"] = dict(evidence_exception)
    return result


def _surface_meeting_gate(
    surface: dict[str, Any],
    dbtl_config: Any,
    *,
    stage: dict[str, Any] | None,
    cycle: dict[str, Any] | None = None,
):
    """The review-meeting gate for this surface's stage, or ``None``.

    The assessment is read off the surface's own server-owned transition gate,
    the same place the progressive Design gate reads it from, so a deck reports
    the difficulty it was rendered against rather than one recomputed now. A
    surface carrying no assessment falls back to ``standard`` inside
    ``surface_meeting_gate`` rather than being treated as routine.
    """
    surface_stage = str(surface.get("stage") or "design")
    transition_gate = _surface_transition_gate(surface) or {}
    assessment = transition_gate.get("assessment") if isinstance(transition_gate.get("assessment"), dict) else {}
    meetings = getattr(dbtl_config, "stage_meetings", None)
    enabled = bool(meetings.enabled_for(surface_stage)) if meetings is not None and hasattr(meetings, "enabled_for") else False
    return surface_meeting_gate(
        stage=surface_stage,
        assessed_difficulty=str((assessment or {}).get("difficulty") or ""),
        enabled=enabled,
        # A stage attempt already carrying its review-meeting artifact has had
        # its meeting; offering another would let the gate demand meetings
        # recursively, which the policy layer explicitly forbids. Read off the
        # artifact rather than a stored flag, so the gate and the evidence a
        # reviewer opens cannot disagree.
        meeting_completed=review_meeting_recorded(
            stage=surface_stage,
            stage_attempt_id=str((stage or {}).get("id") or ""),
            artifacts=[item for item in (cycle or {}).get("artifacts", []) if isinstance(item, dict)],
            evidence_artifact_id=str(surface.get("evidence_artifact_id") or ""),
            evidence_artifact_revision=int(surface.get("evidence_artifact_revision") or 0),
            evidence_content_hash=str(surface.get("evidence_content_hash") or ""),
        ),
    )


def _route_available(gate: dict[str, Any], slug: str) -> bool:
    return any(str(route.get("slug") or "") == slug for route in gate.get("routes", []) if isinstance(route, dict))


def _slide_feedback_text(surface: dict[str, Any], comments: dict[str, str]) -> str:
    """Render validated slide notes without losing their server-owned labels."""
    request_payload = surface.get("decision_request")
    registered = request_payload.get("commentable_slides") if isinstance(request_payload, dict) else []
    if not isinstance(registered, list):
        registered = []
    titles = {str(item.get("id") or ""): str(item.get("title") or item.get("id") or "Slide") for item in registered if isinstance(item, dict)}
    return "\n".join(f'Slide "{titles.get(slide_id, slide_id)}" [{slide_id}]: {comment}' for slide_id, comment in comments.items())


def _exploratory_actions(surface_stage: str) -> list[str]:
    """The exploratory closeout, offered only where the question is asked.

    The caller decides *when*, and the two call sites are not symmetric. On an
    ``awaiting_review`` Build it is offered unconditionally, which is the
    ordinary path and does not need the progressive gate. On an ``in_progress``
    Build it rides inside the one-click gate branch, because recording a
    verdict on an unsubmitted stage needs ``auto_submit``, and that is only
    reachable when a transition gate exists — offering it there without one
    would render a button whose own request the repository then refuses.
    """
    if surface_stage != "build" or not conditional_test_enabled():
        return []
    return ["learn_exploratory"]


def _evidence_exception_review_actions(
    *,
    stage: str,
    stage_status: str,
    evidence_exception: dict[str, Any],
    enabled: bool,
    route_slugs: set[str],
) -> list[str] | None:
    """Return exception controls only for the stage whose evidence failed.

    Learn inherits the dossier as a scientific restriction, but its deck
    reviews the new process-learning evidence. Treating that inherited banner
    as a second Build/Test exception leaves Learn's human gate permanently
    read-only because Learn deliberately cannot record either exception intent.
    """
    if stage not in {"build", "test"} or not evidence_exception or not enabled or stage_status not in {"in_progress", "changes_requested", "awaiting_review"}:
        return None
    if stage == "test" and "learn_from_invalidated_evidence" not in route_slugs:
        incomplete_degraded_pack = evidence_exception.get("condition") == "degraded_verified" and not evidence_exception.get("available_artifacts")
        return ["retry_with_guidance"] if not route_slugs or incomplete_degraded_pack else None
    return ["retry_with_guidance", "continue_with_red_flag"]


async def _post_design_meeting_turn(
    request: Request,
    *,
    thread_id: str,
    run_id: str,
    surface_id: str,
    design_round: int,
    choice_label: str,
    comment: str,
) -> None:
    """Put a deck-started meeting into normal transcript order.

    This is deliberately an ordinary assistant message, not a bespoke dynamic
    card. Its run id binds the message to the background round, whose ordinary
    reply and successor deck then follow beneath it.
    """
    recorded = f"Recorded decision: **{choice_label}**."
    if comment:
        recorded += f"\n\nComment: {comment}"
    message = AIMessage(
        id=f"dbtl-meeting-turn__{surface_id}__{run_id}",
        content=(f"**Design meeting · Round {design_round}**\n\n{recorded}\n\nThe chair is revisiting the recorded positions and will place the follow-up slide deck below this meeting."),
        additional_kwargs={
            "design_feedback_surface_id": surface_id,
            "run_id": run_id,
        },
    )
    try:
        await get_run_event_store(request).put_if_absent(
            thread_id=thread_id,
            run_id=run_id,
            event_type="llm.ai.response",
            category="message",
            content=message.model_dump(),
            metadata={
                "caller": "lead_agent",
                "dbtl_meeting_turn": True,
            },
        )
    except Exception:  # noqa: BLE001 - the admitted chair run must not be rolled back
        logger.exception(
            "Failed to publish Design meeting turn to thread %s for run %s",
            thread_id,
            run_id,
        )


def _latest_worker_failure_detail(
    workers: list[dict[str, Any]],
    *,
    capability: str,
) -> str:
    """Return the newest bounded worker refusal a person can act on."""
    prefix = "The worker's result did not satisfy the stage contract: "
    for worker in reversed(workers):
        if str(worker.get("capability") or "") != capability:
            continue
        if str(worker.get("status") or "") != "failed":
            continue
        result = worker.get("result")
        summary = str(result.get("summary") or "").strip() if isinstance(result, dict) else ""
        if summary.startswith(prefix):
            summary = summary[len(prefix) :]
        if summary:
            return summary[:800]
    return ""


def _next_open_stage(cycle: dict[str, Any], approved_stage: str) -> str | None:
    """The newly opened stage after a deck approval, from durable stage rows."""
    statuses = {str(item.get("stage") or ""): str(item.get("status") or "") for item in cycle.get("stages", []) if isinstance(item, dict)}
    try:
        approved_index = STAGE_ORDER.index(approved_stage)
    except ValueError:
        return None
    return next(
        (stage for stage in STAGE_ORDER[approved_index + 1 :] if statuses.get(stage) == "in_progress"),
        None,
    )


def _stage_advanced_with_exception(cycle: dict[str, Any], stage: str) -> bool:
    return any(item.get("stage") == stage and item.get("status") == "advanced_with_exception" for item in cycle.get("stages", []))


async def _start_post_approval_handoff(
    request: Request,
    *,
    cycle: dict[str, Any],
    approved_stage: str,
    project_id: str,
    thread_id: str,
    surface_id: str,
) -> _PostApprovalHandoff:
    """Start the deterministic chat turn that asks before running the next stage."""
    next_stage = _next_open_stage(cycle, approved_stage)
    if next_stage is None:
        return _PostApprovalHandoff(status="not_needed")
    marker = {
        "version": 1,
        "cycle_id": str(cycle.get("id") or ""),
        "cycle_revision": int(cycle.get("db_revision") or 0),
        "approved_stage": approved_stage,
        "next_stage": next_stage,
        "surface_id": surface_id,
        "advanced_with_exception": _stage_advanced_with_exception(cycle, approved_stage),
    }
    try:
        record = await start_run(
            RunCreateRequest(
                input={
                    "messages": [
                        {
                            "role": "user",
                            # An explicit id is what makes this run's card
                            # reach durable thread history. ``RunJournal``
                            # reconciles a graph-authored ``ask_clarification``
                            # pair only for messages appended *after* the run's
                            # own input, and it recognizes that input by
                            # identity. ``convert_to_messages`` mints no id, so
                            # without this the handoff run completed, held the
                            # card in its final state, and persisted no message
                            # event at all — the control existed and nobody
                            # could see it.
                            "id": f"dbtl-handoff-input__{uuid4().hex}",
                            "content": (f"{approved_stage.title()} approval is recorded. Ask the project owner before starting {next_stage.title()}."),
                            "additional_kwargs": {
                                "hide_from_ui": True,
                                "dbtl_post_approval_handoff": marker,
                            },
                        }
                    ]
                },
                context={
                    "dbtl_supervisor_enabled": True,
                    "dbtl_explicit_choice": "continue_cycle",
                    "dbtl_selected_cycle_id": str(cycle.get("id") or ""),
                },
                on_disconnect="continue",
            ),
            thread_id,
            request,
        )
    except Exception as exc:  # noqa: BLE001 - approval is already durable; delivery is retryable
        failure_code = type(exc).__name__[:64]
        logger.exception(
            "Approval for %s/%s was recorded, but its chat handoff could not start",
            project_id,
            cycle.get("id"),
        )
        _feedback_event(
            "design_feedback.handoff_start_failed",
            surface_id=surface_id,
            project_id=project_id,
            cycle_id=str(cycle.get("id") or ""),
            thread_id=thread_id,
            action_kind="approve",
            revision=int(cycle.get("db_revision") or 0),
            failure_code=failure_code,
        )
        return _PostApprovalHandoff(
            status="failed",
            next_stage=next_stage,
            failure_code=failure_code,
        )
    _feedback_event(
        "design_feedback.handoff_started",
        surface_id=surface_id,
        project_id=project_id,
        cycle_id=str(cycle.get("id") or ""),
        thread_id=thread_id,
        action_kind="approve",
        revision=int(cycle.get("db_revision") or 0),
    )
    return _PostApprovalHandoff(
        status="started",
        next_stage=next_stage,
        run_id=record.run_id,
    )


class CycleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    cycle_class: Literal["season/program", "computational", "other"]
    cycle_weight: CycleWeight = "full"
    research_question: str = Field(min_length=1, max_length=4000)
    objective: str = Field(default="", max_length=4000)
    success_criteria: str = Field(default="", max_length=4000)
    parent_cycle_id: str | None = Field(default=None, max_length=64)
    originating_thread_id: ThreadId | None = None
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
    # Optional, matching the registered-deck path: a reviewer who has nothing
    # to add should not have to invent a sentence to record a decision. The
    # server writes a labelled projection when it is blank, and
    # ``rationale_source`` says which of the two the record holds.
    rationale: str = Field(default="", max_length=10_000)
    expected_db_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("rationale")
    @classmethod
    def rationale_is_trimmed(cls, value: str) -> str:
        return value.strip()


#: Written when a reviewer records a verdict without adding words of their own.
#: Labelled ``rationale_source: server_projection`` so a reader can always tell
#: a generated sentence from the reviewer's.
_DEFAULT_REVIEW_RATIONALE: dict[str, str] = {
    "approve": "Approved against the evidence revision shown at the time of the decision.",
    "request_changes": "Returned to work for revision; no rationale text was recorded.",
}


class DesignFeedbackEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1, max_length=96)
    revision: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DesignFeedbackAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "chair_option",
        "chair_text",
        "submit_for_review",
        "approve",
        "learn_exploratory",
        "continue_with_red_flag",
        "retry_with_guidance",
        "request_changes",
        "reject",
        "advance",
        "park",
        "convene_review_meeting",
        "choose_route",
        "recommend_promotion",
        "close_without_candidate",
    ]
    option_ids: list[str] = Field(default_factory=list, max_length=16)
    difficulty_override: Literal["routine", "standard", "high_stakes"] | None = None

    @field_validator("option_ids")
    @classmethod
    def option_ids_are_bounded_slugs(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("option_ids must be unique")
        for value in values:
            if not value or len(value) > 64 or not value.replace("-", "").replace("_", "").isalnum():
                raise ValueError("option_ids must be bounded slug identifiers")
        return values


class DesignFeedbackActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    action: DesignFeedbackAction
    comment: str = Field(default="", max_length=4_000)
    slide_comments: dict[str, str] = Field(default_factory=dict, max_length=20)
    active_slide_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    client_submission_id: str = Field(min_length=1, max_length=128)
    originating_thread_id: str = Field(min_length=1, max_length=64)
    expected_db_revision: int = Field(ge=1)
    expected_evidence: DesignFeedbackEvidence | None = None
    expected_deck_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("slide_comments")
    @classmethod
    def slide_comments_are_bounded(cls, values: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        total = 0
        for raw_id, raw_comment in values.items():
            slide_id = raw_id.strip()
            if not slide_id or len(slide_id) > 64 or not slide_id.replace("-", "").replace("_", "").isalnum():
                raise ValueError("slide comment ids must be bounded slugs")
            comment = raw_comment.strip()
            if not comment:
                continue
            if len(comment) > 2_000:
                raise ValueError("each slide comment is limited to 2000 characters")
            total += len(comment)
            if total > 10_000:
                raise ValueError("slide comments are limited to 10000 characters in total")
            normalized[slide_id] = comment
        return normalized


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


def _require_knowledge_authority(project: dict[str, Any]) -> None:
    if str(project.get("current_user_role") or "").strip().lower() not in KNOWLEDGE_AUTHORITY_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a project owner or administrator may alter governed knowledge.",
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
    if isinstance(exc, DesignFeedbackConflict):
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
async def get_cycle(project_id: str, cycle_id: str, request: Request, repo=Depends(get_dbtl_cycle_repo), config: AppConfig = Depends(get_config)):
    await _require_project(project_id, request)
    cycle = await repo.get_cycle(cycle_id, project_id=project_id)
    if cycle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")
    dbtl_config = getattr(request.app.state, "dbtl_config_override", config.dbtl)
    if dbtl_config.progressive_gate:
        # The read model for the path strip. Records accumulate regardless of
        # the flag; only their exposure is gated, so turning the flag on shows
        # the history that was already being kept.
        cycle["transitions"] = await repo.list_stage_transitions(cycle_id=cycle_id, project_id=project_id)
        surface = await repo.latest_stage_feedback_surface(
            project_id=project_id,
            cycle_id=cycle_id,
            stage="design",
            mode="stage_review",
        )
        gate = _surface_transition_gate(surface or {})
        design = next(
            (item for item in cycle.get("stages", []) if item.get("stage") == "design"),
            None,
        )
        attempt_artifacts = _reviewable_attempt_artifacts("design", [item for item in cycle.get("artifacts", []) if surface is not None and item.get("stage_attempt_id") == surface.get("stage_attempt_id")])
        newest_evidence = max(
            attempt_artifacts,
            key=lambda item: int(item.get("revision") or 0),
            default=None,
        )
        evidence_matches = bool(
            surface is not None
            and newest_evidence
            and newest_evidence.get("id") == surface.get("evidence_artifact_id")
            and newest_evidence.get("revision") == surface.get("evidence_artifact_revision")
            and newest_evidence.get("content_hash") == surface.get("evidence_content_hash")
        )
        gate_is_pending = str((design or {}).get("status") or "") in {
            "in_progress",
            "changes_requested",
            "awaiting_review",
        }
        if surface is not None and gate is not None and evidence_matches and gate_is_pending:
            cycle["transition_gate"] = {
                **gate,
                "surface_id": surface["surface_id"],
                "deck_uri": surface["deck_uri"],
                "originating_thread_id": surface["originating_thread_id"],
                "parked": bool(cycle.get("parked", False)),
            }
    return cycle


@router.get("/projects/{project_id}/dbtl/cycles/{cycle_id}/activity")
@require_permission("threads", "read")
async def list_activity(project_id: str, cycle_id: str, request: Request, repo=Depends(get_dbtl_cycle_repo)):
    await _require_project(project_id, request)
    return {"cycle_id": cycle_id, "events": await repo.list_activity(cycle_id, project_id=project_id)}


async def _design_feedback_read_model(
    project_id: str,
    surface_id: str,
    request: Request,
    *,
    cycle_id: str | None,
    viewer_thread_id: str | None,
    config: AppConfig,
    repo,
) -> dict[str, Any]:
    _project, user_id = await _require_project(project_id, request)
    surface = await repo.get_stage_feedback_surface(surface_id, project_id=project_id)
    if surface is None or (cycle_id is not None and surface["cycle_id"] != cycle_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback surface not found")

    resolved_cycle_id = str(surface["cycle_id"])
    surface_stage = str(surface.get("stage") or "design")
    newest = await repo.latest_stage_feedback_surface(
        project_id=project_id,
        cycle_id=resolved_cycle_id,
        stage=surface_stage,
    )
    cycle = await repo.get_cycle(resolved_cycle_id, project_id=project_id)
    actions = await repo.stage_feedback_actions(surface_id, project_id=project_id)
    latest_action = actions[-1] if actions else None
    # A chair answer or review-meeting request starts a background run. A
    # provider/executor failure can
    # still leave that parent run terminal-successful because the failed worker
    # is an audited stage outcome rather than an exception. If the run is
    # terminal and produced no successor feedback surface, the answer did not
    # yield a chair outcome. Release the same payload-bound action for retry
    # instead of leaving the only deck permanently consumed.
    background_round = bool(
        latest_action and ((surface["mode"] == "chair_feedback" and latest_action.get("action_group") == "chair_response") or (surface["mode"] == "stage_review" and latest_action.get("action_group") == "review_meeting"))
    )
    if background_round and surface["is_current"] and latest_action and latest_action.get("status") == "resume_started" and latest_action.get("run_id"):
        try:
            run = await get_run_store(request).get(
                str(latest_action["run_id"]),
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001 - an unreadable run cannot prove failure
            run = None
        raw_status = (run or {}).get("status") if isinstance(run, dict) else None
        run_status = str(getattr(raw_status, "value", raw_status) or "")
        if run_status in {"success", "error", "timeout", "interrupted"}:
            round_label = "Design chair" if surface["mode"] == "chair_feedback" else f"{surface_stage.title()} review meeting"
            failure_detail = ""
            if surface["mode"] == "chair_feedback":
                try:
                    workers = await repo.list_worker_runs(
                        resolved_cycle_id,
                        project_id=project_id,
                        stage="design",
                    )
                    failure_detail = _latest_worker_failure_detail(
                        workers,
                        capability="design_council_chair",
                    )
                except Exception:  # noqa: BLE001 - the generic refusal still reopens the deck
                    logger.warning(
                        "Could not read the rejected Design chair result for feedback surface %s",
                        surface_id,
                        exc_info=True,
                    )
            if failure_detail:
                message = f"The {round_label}'s response was rejected: {failure_detail} Edit your answer if needed, then send it again."
            else:
                message = f"The {round_label} ended without a follow-up deck. Edit your answer if needed, then send it again."
            latest_action = await repo.update_stage_feedback_action(
                str(latest_action["client_submission_id"]),
                project_id=project_id,
                status="failed",
                receipt={
                    "kind": latest_action.get("action_kind"),
                    "run_id": latest_action.get("run_id"),
                    "run_status": run_status,
                    "message": message,
                    **({"failure_detail": failure_detail} if failure_detail else {}),
                },
                failure_code="resume_no_feedback_surface",
            )
    handoff_receipt = dict((latest_action or {}).get("receipt") or {})
    if latest_action and latest_action.get("action_group") == "stage_review" and latest_action.get("status") == "review_recorded" and handoff_receipt.get("handoff_status") == "started" and latest_action.get("run_id"):
        try:
            run = await get_run_store(request).get(
                str(latest_action["run_id"]),
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001 - an unreadable run cannot prove failure
            run = None
        raw_status = (run or {}).get("status") if isinstance(run, dict) else None
        run_status = str(getattr(raw_status, "value", raw_status) or "")
        if run_status == "success":
            # The deck action starts this run outside the mounted chat's
            # LangGraph stream.  A successful run therefore does not wake the
            # conversation by itself.  Publish a durable convergence signal
            # only after the same message projection used by chat can see the
            # Start/Hold card; the browser polls this read model and invalidates
            # the thread at that point.
            delivered = await _handoff_card_is_visible(
                request,
                thread_id=str(surface["originating_thread_id"]),
                run_id=str(latest_action["run_id"]),
                user_id=user_id,
            )
            if delivered:
                handoff_receipt.update(
                    {
                        "handoff_status": "delivered",
                        "message": "Approval recorded. The next-stage choice is ready in this conversation.",
                    }
                )
                handoff_receipt.pop("handoff_failure_code", None)
                latest_action, _changed = await repo.transition_stage_feedback_handoff(
                    str(latest_action["client_submission_id"]),
                    project_id=project_id,
                    run_id=str(latest_action["run_id"]),
                    status="review_recorded",
                    receipt=handoff_receipt,
                    failure_code=None,
                )
            elif await _handoff_delivery_is_settled(
                request,
                thread_id=str(surface["originating_thread_id"]),
                run_id=str(latest_action["run_id"]),
            ):
                # A Gateway restart loses the detached watcher. The terminal
                # delivery receipt is the durable ordering fence: the journal
                # flushed every message before it, so a missing card is now a
                # completed empty delivery rather than eventual consistency.
                handoff_receipt.update(
                    {
                        "handoff_status": "failed",
                        "handoff_failure_code": "success_without_follow_up",
                        "message": (f"{str(handoff_receipt.get('approved_stage') or surface_stage).title()} approval remains recorded, but the next-stage prompt stopped before it appeared. Retry the same decision from this deck."),
                    }
                )
                latest_action, _changed = await repo.transition_stage_feedback_handoff(
                    str(latest_action["client_submission_id"]),
                    project_id=project_id,
                    run_id=str(latest_action["run_id"]),
                    status="handoff_failed",
                    receipt=handoff_receipt,
                    failure_code="handoff_success_without_follow_up",
                )
        elif run_status in {"error", "timeout", "interrupted"}:
            handoff_receipt.update(
                {
                    "handoff_status": "failed",
                    "handoff_failure_code": f"run_{run_status}"[:64],
                    "message": (f"{str(handoff_receipt.get('approved_stage') or surface_stage).title()} approval remains recorded, but the next-stage prompt stopped before it appeared. Retry the same decision from this deck."),
                }
            )
            latest_action, _changed = await repo.transition_stage_feedback_handoff(
                str(latest_action["client_submission_id"]),
                project_id=project_id,
                run_id=str(latest_action["run_id"]),
                status="handoff_failed",
                receipt=handoff_receipt,
                failure_code=f"handoff_run_{run_status}"[:64],
            )
    # Whether this deck may still act. Supersession records what somebody was
    # most recently *shown*, which is not the same question: a later round that
    # produced no package renders a ``read_only`` deck, and reading that as
    # revoking the reviewable package's own deck leaves a Design awaiting a
    # verdict with no surface that can record one. A stage_review deck is live
    # while no newer stage_review deck exists for the same attempt; every other
    # mode is live only while nothing at all supersedes it.
    surface_is_live = bool(surface["is_current"])
    evidence_matches = True
    if surface["mode"] == "stage_review":
        newest_review = await repo.latest_stage_feedback_surface(
            project_id=project_id,
            cycle_id=resolved_cycle_id,
            stage=surface_stage,
            stage_attempt_id=surface.get("stage_attempt_id"),
            mode="stage_review",
        )
        surface_is_live = newest_review is None or str(newest_review["surface_id"]) == surface_id
        # Supplemental dossiers and review-meeting packages annotate the core
        # stage evidence. They must never replace the Build/Test/Learn artifact
        # this deck is actually asking a person to review.
        attempt_artifacts = _reviewable_attempt_artifacts(surface_stage, [item for item in (cycle or {}).get("artifacts", []) if item.get("stage_attempt_id") == surface.get("stage_attempt_id")])
        artifact = max(
            attempt_artifacts,
            key=lambda item: int(item.get("revision") or 0),
            default=None,
        )
        evidence_matches = bool(
            artifact and artifact.get("id") == surface.get("evidence_artifact_id") and artifact.get("revision") == surface.get("evidence_artifact_revision") and artifact.get("content_hash") == surface.get("evidence_content_hash")
        )

    dbtl_config = getattr(request.app.state, "dbtl_config_override", config.dbtl)
    interactive = False
    allowed_actions: list[str] = []
    user = getattr(request.state, "user", None)
    viewer_matches = bool(viewer_thread_id and viewer_thread_id == surface["originating_thread_id"])
    if viewer_matches:
        try:
            thread = await get_thread_store(request).get(str(viewer_thread_id), user_id=user_id)
            viewer_matches = bool(thread and thread.get("project_id") == project_id)
        except Exception:  # noqa: BLE001 - inability to prove scope means inert
            viewer_matches = False

    if dbtl_config.design_deck_feedback and dbtl_config.mutations_enabled and getattr(user, "system_role", None) != INTERNAL_SYSTEM_ROLE and viewer_matches and surface_is_live and evidence_matches and cycle is not None:
        stage = next((item for item in cycle["stages"] if item["stage"] == surface_stage), None)
        stage_status = str((stage or {}).get("status") or "")
        groups = {str(item["action_group"]): item for item in actions}
        if latest_action is not None:
            # Recovery above may have changed this row after ``actions`` was
            # loaded; the read model must use the reconciled status immediately
            # rather than requiring one more poll to become actionable.
            groups[str(latest_action["action_group"])] = latest_action
        if surface["mode"] == "chair_feedback" and surface.get("human_input_request_id"):
            chair = groups.get("chair_response")
            if chair is None or chair.get("status") == "failed":
                request_payload = surface.get("decision_request")
                options = request_payload.get("options") if isinstance(request_payload, dict) else None
                allowed_actions = ["chair_option"] if options else ["chair_text"]
        elif surface["mode"] == "stage_review":
            exception_gate = _surface_transition_gate(surface) or {}
            evidence_exception = exception_gate.get("evidence_exception")
            evidence_exception = dict(evidence_exception) if isinstance(evidence_exception, dict) else {}
            stage_review_action = groups.get("stage_review")
            if stage_review_action is None or stage_review_action.get("status") == "failed":
                gate = _surface_transition_gate(surface) if dbtl_config.progressive_gate else None
                exception_actions = _evidence_exception_review_actions(
                    stage=surface_stage,
                    stage_status=stage_status,
                    evidence_exception=evidence_exception,
                    enabled=dbtl_config.degraded_evidence_continuation,
                    route_slugs={str(item.get("slug") or "") for item in (gate or {}).get("routes", []) if isinstance(item, dict)},
                )
                if exception_actions is not None:
                    allowed_actions = exception_actions
                elif stage_status in {"in_progress", "changes_requested"} and "stage_submit" not in groups:
                    if gate is not None:
                        # The simple gate card: Approve, Revise, Park — each
                        # recorded in one action. Legacy decks still render a
                        # submit button, so that intent stays allowed.
                        #
                        # `approve` is offered whether or not the Build edge is
                        # open. A blocked edge means Build stays locked after
                        # the verdict, never that the verdict cannot be given:
                        # approving Design is what opens the reconciliation
                        # work the edge is waiting on, so gating it on the edge
                        # deadlocks the cycle.
                        allowed_actions = ["submit_for_review", "request_changes", "approve"]
                        if surface_stage == "design" and "stage_park" not in groups:
                            allowed_actions.append("park")
                        if _route_available(gate, "advance"):
                            allowed_actions.append("advance")
                        allowed_actions.extend(_exploratory_actions(surface_stage))
                    else:
                        allowed_actions = ["submit_for_review"]
                elif stage_status == "awaiting_review":
                    allowed_actions = ["choose_route"] if surface_stage == "test" else ["approve", "request_changes", "reject"]
                    if gate is not None and surface_stage == "design" and "stage_park" not in groups:
                        allowed_actions.append("park")
                    allowed_actions.extend(_exploratory_actions(surface_stage))
        # The convening decision is folded in before the stage's own intent
        # matrix has the last word: the gate may add ``convene_review_meeting``
        # or withhold the transition intents, but it can never grant a stage an
        # intent that stage may not ever record.
        allowed_actions = apply_meeting_gate(_surface_meeting_gate(surface, dbtl_config, stage=stage, cycle=cycle), allowed_actions)
        allowed_actions = filter_stage_feedback_intents(surface_stage, allowed_actions)
        if latest_action is not None and latest_action.get("status") == "handoff_failed":
            retry_kind = str(latest_action.get("action_kind") or "")
            allowed_actions = [retry_kind] if retry_kind in {"approve", "advance", "learn_exploratory", "continue_with_red_flag"} else []
        elif latest_action is not None and latest_action.get("status") == "failed" and latest_action.get("action_kind") == "retry_with_guidance":
            allowed_actions = ["retry_with_guidance"]
        interactive = bool(allowed_actions)

    note = ""
    if not surface_is_live:
        note = f"A newer {surface_stage.title()} surface replaced this deck."
    elif not evidence_matches:
        note = f"The {surface_stage.title()} evidence changed after this deck was rendered. Regenerate the feedback deck."
    elif not viewer_matches:
        note = f"Open this deck in the conversation where the {surface_stage.title()} work started."
    elif not dbtl_config.design_deck_feedback:
        note = "Design deck feedback is disabled; use the fallback Design controls."
    elif latest_action and latest_action.get("status") in {
        "accepted",
        "resume_started",
        "review_recorded",
    }:
        receipt = latest_action.get("receipt")
        note = str(receipt.get("message")) if isinstance(receipt, dict) and receipt.get("message") else "This feedback step has already been recorded."
    elif latest_action and latest_action.get("status") in {"failed", "handoff_failed"}:
        receipt = latest_action.get("receipt")
        note = str(receipt.get("message")) if isinstance(receipt, dict) and receipt.get("message") else "The previous attempt did not produce a follow-up deck. Try sending your answer again."
    elif cycle and cycle.get("parked"):
        note = "This cycle is parked. Ordinary cycle-scoped requests go to the lead agent with the bound Design package clearly marked unapproved."

    logger.info(
        "design_feedback.surface_opened",
        extra={
            "design_feedback": {
                "project_id": project_id,
                "cycle_id": resolved_cycle_id,
                "thread_id": viewer_thread_id or "",
                "surface_id": surface_id,
                "round": surface["design_round"],
                "interactive": interactive,
            }
        },
    )
    lifecycle_state = "superseded" if not surface_is_live else "consumed" if not interactive and latest_action is not None and latest_action.get("status") not in {"failed", "handoff_failed", "pending"} else "open"
    return {
        **surface,
        "lifecycle_state": lifecycle_state,
        "newest_surface_id": (newest or {}).get("surface_id"),
        "newest_surface_uri": (newest or {}).get("deck_uri"),
        "allowed_actions": allowed_actions,
        "interactive": interactive,
        "current_db_revision": int(cycle["db_revision"]) if cycle else None,
        "current_stage_status": (next((item["status"] for item in cycle["stages"] if item["stage"] == surface_stage), None) if cycle else None),
        "originating_conversation_id": surface["originating_thread_id"],
        "receipt": latest_action,
        "note": note,
        "transition_gate": (_surface_transition_gate(surface) if dbtl_config.progressive_gate else None),
        # Served in every mode, like the transition gate: a client that cannot
        # see why its deck offers no meeting cannot explain it either. Null for
        # Design, which has no review meeting.
        "meeting_gate": (lambda gate: gate.as_dict() if gate is not None else None)(
            _surface_meeting_gate(
                surface,
                dbtl_config,
                stage=next((item for item in cycle["stages"] if item["stage"] == surface_stage), None) if cycle else None,
                cycle=cycle,
            )
        ),
        "parked": bool((cycle or {}).get("parked", False)),
    }


@router.get("/projects/{project_id}/dbtl/cycles/{cycle_id}/design-feedback/{surface_id}")
@router.get("/projects/{project_id}/dbtl/cycles/{cycle_id}/stage-feedback/{surface_id}")
@require_permission("threads", "read")
async def get_design_feedback_surface(
    project_id: str,
    cycle_id: str,
    surface_id: str,
    request: Request,
    viewer_thread_id: str | None = None,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    return await _design_feedback_read_model(
        project_id,
        surface_id,
        request,
        cycle_id=cycle_id,
        viewer_thread_id=viewer_thread_id,
        config=config,
        repo=repo,
    )


@router.get("/projects/{project_id}/dbtl/design-feedback/{surface_id}")
@router.get("/projects/{project_id}/dbtl/stage-feedback/{surface_id}")
@require_permission("threads", "read")
async def resolve_design_feedback_surface(
    project_id: str,
    surface_id: str,
    request: Request,
    viewer_thread_id: str | None = None,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    """Resolve a deck from its embedded opaque id without trusting deck routing."""
    return await _design_feedback_read_model(
        project_id,
        surface_id,
        request,
        cycle_id=None,
        viewer_thread_id=viewer_thread_id,
        config=config,
        repo=repo,
    )


def _feedback_event(
    event: str,
    *,
    surface_id: str,
    project_id: str,
    cycle_id: str,
    thread_id: str,
    action_kind: str,
    revision: int,
    failure_code: str | None = None,
) -> None:
    logger.info(
        event,
        extra={
            "design_feedback": {
                "project_id": project_id,
                "cycle_id": cycle_id,
                "thread_id": thread_id,
                "surface_id": surface_id,
                "action_kind": action_kind,
                "revision": revision,
                "failure_code": failure_code,
            }
        },
    )


def _watch_round_if_possible(
    request: Request,
    *,
    user_id: str,
    thread_id: str,
    run_id: str,
    surface_id: str,
    explanation: str,
    success_has_follow_up: Callable[[], Awaitable[bool]] | None = None,
    on_failure: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Spawn the failed-round announcer; a visibility aid must never fail the verdict."""
    try:
        run_store = get_run_store(request)
        event_store = get_run_event_store(request)
    except HTTPException:
        logger.warning("Round-failure watcher unavailable for run %s: stores not configured", run_id)
        return
    watch_background_round(
        run_store,
        event_store,
        user_id=user_id,
        thread_id=thread_id,
        run_id=run_id,
        surface_id=surface_id,
        explanation=explanation,
        success_has_follow_up=success_has_follow_up,
        on_failure=on_failure,
    )


def _handoff_receipt(
    receipt: dict[str, Any],
    *,
    approved_stage: str,
    handoff: _PostApprovalHandoff,
    decision_label: str = "approval",
) -> dict[str, Any]:
    """Keep review authority separate from best-effort chat delivery."""
    updated = {
        **receipt,
        "approved_stage": approved_stage,
        "next_stage": handoff.next_stage,
        "handoff_status": handoff.status,
        "handoff_run_id": handoff.run_id,
    }
    if handoff.status == "failed":
        updated["message"] = f"{approved_stage.title()} {decision_label} is recorded, but the next-stage prompt could not start. Reopen this deck and retry the same decision; it will not be recorded twice."
        updated["handoff_failure_code"] = handoff.failure_code
    elif handoff.status == "started":
        updated["message"] = f"{decision_label.capitalize()} recorded. Choose the next governed action in the originating conversation."
        updated.pop("handoff_failure_code", None)
    else:
        updated["message"] = f"{approved_stage.title()} {decision_label} is recorded. No later stage is currently waiting for a start decision."
        updated.pop("handoff_failure_code", None)
    return updated


async def _human_input_card_is_visible(
    request: Request,
    *,
    thread_id: str,
    run_id: str,
    user_id: str,
    clarification_type: str,
) -> bool:
    """Whether one server-owned control reached durable thread history.

    A successful run is not a delivered control. The 2026-08-01 replay completed
    without error and held the card in its final graph state, yet persisted no
    message event — so the card was absent live *and* after refresh, and the
    owner's next message escaped to the lead agent. Delivery is therefore
    verified against the same projection the conversation reads
    (``GET /threads/{id}/messages/page``), not against run status.

    An unconfigured store returns ``True``: with nothing to read there is no
    delivery to verify, and announcing a failure on that basis would be noise.
    A *read error* is deliberately allowed to propagate instead. The caller
    polls this on a 5-second loop and already retries a raising callback, so
    swallowing the first transient error ended the watch permanently and
    reported "delivered" — precisely when a struggling store is also the most
    likely reason the card is missing.
    """
    try:
        event_store = get_run_event_store(request)
    except HTTPException:
        return True
    rows = await event_store.list_messages_by_run(thread_id, run_id, limit=50, user_id=user_id)
    for row in rows or []:
        content = row.get("content") if isinstance(row, dict) else None
        if not isinstance(content, dict):
            continue
        artifact = content.get("artifact")
        payload = artifact.get("human_input") if isinstance(artifact, dict) else None
        if isinstance(payload, dict) and payload.get("clarification_type") == clarification_type:
            return True
    return False


async def _handoff_card_is_visible(
    request: Request,
    *,
    thread_id: str,
    run_id: str,
    user_id: str,
) -> bool:
    return await _human_input_card_is_visible(
        request,
        thread_id=thread_id,
        run_id=run_id,
        user_id=user_id,
        clarification_type="dbtl_stage_handoff",
    )


def _watch_evidence_retry_delivery(
    request: Request,
    *,
    repo,
    user_id: str,
    project_id: str,
    thread_id: str,
    surface_id: str,
    action_id: str,
    run_id: str,
    stage: str,
) -> None:
    """Reopen the deck action if its chat guidance control never arrives."""

    async def card_delivered() -> bool:
        visible = await _human_input_card_is_visible(
            request,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user_id,
            clarification_type="dbtl_evidence_retry",
        )
        if not visible and not await _handoff_delivery_is_settled(
            request,
            thread_id=thread_id,
            run_id=run_id,
        ):
            raise RuntimeError("Evidence-retry delivery is still finalizing")
        return visible

    async def mark_failed(run_status: str) -> None:
        actions = await repo.stage_feedback_actions(surface_id, project_id=project_id)
        current = next(
            (item for item in actions if str(item.get("client_submission_id") or "") == action_id),
            None,
        )
        if current is None or current.get("status") != "resume_started" or str(current.get("run_id") or "") != run_id:
            return
        await repo.update_stage_feedback_action(
            action_id,
            project_id=project_id,
            status="failed",
            run_id=run_id,
            receipt={
                "kind": "retry_with_guidance",
                "run_id": run_id,
                "run_status": run_status,
                "message": f"The {stage.title()} retry control stopped before it appeared. Edit the guidance if needed, then send it again from this deck.",
            },
            failure_code=f"retry_control_{run_status}"[:64],
        )

    _watch_round_if_possible(
        request,
        user_id=user_id,
        thread_id=thread_id,
        run_id=run_id,
        surface_id=surface_id,
        explanation=f"The {stage.title()} retry control stopped before it appeared. Reopen the exception deck to retry; no worker ran.",
        success_has_follow_up=card_delivered,
        on_failure=mark_failed,
    )


async def _handoff_delivery_is_settled(
    request: Request,
    *,
    thread_id: str,
    run_id: str,
) -> bool:
    """Whether the run journal has finished writing its message projection."""
    try:
        event_store = get_run_event_store(request)
    except HTTPException:
        return True
    receipts = await event_store.list_events(
        thread_id,
        run_id,
        event_types=["run.delivery"],
        limit=1,
    )
    return bool(receipts)


def _watch_post_approval_handoff(
    request: Request,
    *,
    repo,
    user_id: str,
    project_id: str,
    cycle_id: str,
    thread_id: str,
    surface_id: str,
    action_id: str,
    approved_stage: str,
    handoff: _PostApprovalHandoff,
    decision_label: str = "approval",
) -> None:
    """Make an admitted handoff recoverable if its run dies or delivers nothing."""
    if handoff.status != "started" or not handoff.run_id:
        return

    async def card_delivered() -> bool:
        visible = await _handoff_card_is_visible(
            request,
            thread_id=thread_id,
            run_id=str(handoff.run_id),
            user_id=user_id,
        )
        if not visible and not await _handoff_delivery_is_settled(
            request,
            thread_id=thread_id,
            run_id=str(handoff.run_id),
        ):
            # Run status can become visible before the worker flushes the
            # journal. Absence is not evidence until the terminal delivery
            # receipt establishes that every preceding message has landed.
            raise RuntimeError("Handoff delivery is still finalizing")
        return visible

    async def mark_failed(run_status: str) -> None:
        actions = await repo.stage_feedback_actions(surface_id, project_id=project_id)
        current = next(
            (item for item in actions if str(item.get("client_submission_id") or "") == action_id),
            None,
        )
        if current is None or current.get("status") != "review_recorded" or str(current.get("run_id") or "") != handoff.run_id:
            return
        receipt = dict(current.get("receipt") or {})
        if receipt.get("handoff_status") != "started":
            return
        if await _handoff_card_is_visible(
            request,
            thread_id=thread_id,
            run_id=str(handoff.run_id),
            user_id=user_id,
        ):
            receipt.update(
                {
                    "handoff_status": "delivered",
                    "message": f"{decision_label.capitalize()} recorded. The next-stage choice is ready in this conversation.",
                }
            )
            receipt.pop("handoff_failure_code", None)
            await repo.transition_stage_feedback_handoff(
                action_id,
                project_id=project_id,
                run_id=handoff.run_id,
                status="review_recorded",
                receipt=receipt,
                failure_code=None,
            )
            return
        receipt.update(
            {
                "handoff_status": "failed",
                "handoff_failure_code": f"run_{run_status}"[:64],
                "message": (f"{approved_stage.title()} {decision_label} remains recorded, but the next-stage prompt stopped before it appeared. Reopen this deck and retry the same decision."),
            }
        )
        await repo.transition_stage_feedback_handoff(
            action_id,
            project_id=project_id,
            run_id=handoff.run_id,
            status="handoff_failed",
            receipt=receipt,
            failure_code=f"handoff_run_{run_status}"[:64],
        )

    _watch_round_if_possible(
        request,
        user_id=user_id,
        thread_id=thread_id,
        run_id=handoff.run_id,
        surface_id=surface_id,
        explanation=(f"{approved_stage.title()} {decision_label} is recorded, but the prompt for the next stage stopped before it appeared. Reopen the same feedback deck to retry the handoff."),
        # A run that succeeds without leaving its card in thread history has
        # delivered nothing, so it is treated exactly like one that died: the
        # ledger action reopens for retry and the conversation says so.
        success_has_follow_up=card_delivered,
        on_failure=mark_failed,
    )


@router.post("/projects/{project_id}/dbtl/cycles/{cycle_id}/design-feedback/{surface_id}/actions")
@router.post("/projects/{project_id}/dbtl/cycles/{cycle_id}/stage-feedback/{surface_id}/actions")
@require_permission("threads", "write")
async def apply_design_feedback_action(
    project_id: str,
    cycle_id: str,
    surface_id: str,
    body: DesignFeedbackActionRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_cycle_repo),
):
    """Accept one authenticated intent; the iframe itself receives no authority."""
    project, user_id = await _require_project(project_id, request)
    _require_mutations_enabled(request, config)
    _require_human_reviewer(request)
    dbtl_config = getattr(request.app.state, "dbtl_config_override", config.dbtl)
    if not dbtl_config.design_deck_feedback:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Design deck feedback is disabled; use the fallback Design controls.",
        )
    try:
        originating_thread = await get_thread_store(request).get(
            body.originating_thread_id,
            user_id=user_id,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The originating conversation could not be verified.",
        ) from exc
    if not originating_thread or originating_thread.get("project_id") != project_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The originating conversation is not owned by this user in this project.",
        )

    expected_evidence = body.expected_evidence.model_dump() if body.expected_evidence else None
    try:
        surface, action, replayed = await repo.reserve_stage_feedback_action(
            project_id=project_id,
            cycle_id=cycle_id,
            surface_id=surface_id,
            originating_thread_id=body.originating_thread_id,
            action_kind=body.action.kind,
            selected_card_ids=body.action.option_ids,
            human_comment=body.comment,
            client_submission_id=body.client_submission_id,
            expected_db_revision=body.expected_db_revision,
            expected_evidence=expected_evidence,
            expected_deck_hash=body.expected_deck_hash,
            difficulty_override=body.action.difficulty_override,
            slide_comments=body.slide_comments,
            active_slide_id=body.active_slide_id,
            degraded_evidence_continuation=dbtl_config.degraded_evidence_continuation,
        )
    except Exception as exc:  # noqa: BLE001
        _feedback_event(
            "design_feedback.conflict",
            surface_id=surface_id,
            project_id=project_id,
            cycle_id=cycle_id,
            thread_id=body.originating_thread_id,
            action_kind=body.action.kind,
            revision=body.expected_db_revision,
            failure_code="validation_conflict",
        )
        raise _translate(exc) from exc

    action_id = str(action["client_submission_id"])
    surface_stage = str(surface.get("stage") or "design")
    recorded_slide_comments = {str(key): str(value) for key, value in dict(action.get("slide_comments") or {}).items()}
    active_slide_id = str(action.get("active_slide_id") or "") or None
    slide_feedback = _slide_feedback_text(surface, recorded_slide_comments)
    written_feedback = "\n\n".join(value for value in (body.comment.strip(), slide_feedback) if value)

    async def round_has_follow_up() -> bool:
        latest = await repo.latest_stage_feedback_surface(
            project_id=project_id,
            cycle_id=cycle_id,
            stage=surface_stage,
            stage_attempt_id=surface.get("stage_attempt_id"),
        )
        return bool(latest and str(latest.get("surface_id") or "") != surface_id)

    async def finish_approval_handoff(
        cycle: dict[str, Any],
        *,
        approved_stage: str,
        receipt: dict[str, Any],
    ) -> tuple[dict[str, Any], _PostApprovalHandoff]:
        decision_label = "red-flag continuation" if receipt.get("kind") == "continue_with_red_flag" else "approval"
        handoff = await _start_post_approval_handoff(
            request,
            cycle=cycle,
            approved_stage=approved_stage,
            project_id=project_id,
            thread_id=body.originating_thread_id,
            surface_id=surface_id,
        )
        updated_receipt = _handoff_receipt(
            receipt,
            approved_stage=approved_stage,
            handoff=handoff,
            decision_label=decision_label,
        )
        updated = await repo.update_stage_feedback_action(
            action_id,
            project_id=project_id,
            status="handoff_failed" if handoff.status == "failed" else "review_recorded",
            run_id=handoff.run_id,
            receipt=updated_receipt,
            failure_code=("handoff_start_failed" if handoff.status == "failed" else None),
        )
        _watch_post_approval_handoff(
            request,
            repo=repo,
            user_id=user_id,
            project_id=project_id,
            cycle_id=cycle_id,
            thread_id=body.originating_thread_id,
            surface_id=surface_id,
            action_id=action_id,
            approved_stage=approved_stage,
            handoff=handoff,
            decision_label=decision_label,
        )
        return updated, handoff

    receipt = dict(action.get("receipt") or {})
    if replayed and action.get("status") == "pending" and receipt.get("handoff_status") == "retrying":
        current_cycle = await repo.get_cycle(cycle_id, project_id=project_id)
        if current_cycle is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")
        approved_stage = str(receipt.get("approved_stage") or surface_stage)
        updated, _handoff = await finish_approval_handoff(
            current_cycle,
            approved_stage=approved_stage,
            receipt={
                **receipt,
                "kind": str(action.get("action_kind") or body.action.kind),
                "db_revision": int(current_cycle.get("db_revision") or 0),
            },
        )
        return {**updated, "cycle": current_cycle, "replayed": True}

    if replayed and action["status"] in {"accepted", "resume_started", "review_recorded"}:
        _feedback_event(
            "design_feedback.replayed",
            surface_id=surface_id,
            project_id=project_id,
            cycle_id=cycle_id,
            thread_id=body.originating_thread_id,
            action_kind=body.action.kind,
            revision=body.expected_db_revision,
        )
        return {**action, "replayed": True}

    _feedback_event(
        "design_feedback.intent_received",
        surface_id=surface_id,
        project_id=project_id,
        cycle_id=cycle_id,
        thread_id=body.originating_thread_id,
        action_kind=body.action.kind,
        revision=body.expected_db_revision,
    )
    try:
        if body.action.kind in {"chair_option", "chair_text"}:
            request_payload = dict(surface.get("decision_request") or {})
            question = str(request_payload.get("question") or "the Design meeting's question")
            option = next(
                (item for item in request_payload.get("options", []) if isinstance(item, dict) and item.get("id") in body.action.option_ids),
                None,
            )
            if body.action.kind == "chair_option":
                answer = str((option or {}).get("value") or "")
                response = {
                    "version": 1,
                    "kind": "human_input_response",
                    "source": "ask_clarification",
                    "request_id": surface["human_input_request_id"],
                    "response_kind": "option",
                    "option_id": body.action.option_ids[0],
                    "value": answer,
                }
            else:
                answer = body.comment.strip()
                if not answer:
                    raise DesignFeedbackConflict("A free-text chair answer cannot be empty.")
                response = {
                    "version": 1,
                    "kind": "human_input_response",
                    "source": "ask_clarification",
                    "request_id": surface["human_input_request_id"],
                    "response_kind": "text",
                    "value": answer,
                }
            visible_answer = answer
            if body.action.kind == "chair_option" and written_feedback:
                visible_answer = f"{answer}\n\nReviewer feedback:\n{written_feedback}"
            record = await start_run(
                RunCreateRequest(
                    input={
                        "messages": [
                            {
                                "role": "user",
                                # See ``_start_post_approval_handoff``: without an
                                # explicit identity the journal cannot tell this
                                # run's own messages from retained history, so a
                                # card this run authors never reaches the feed.
                                "id": f"dbtl-chair-answer__{uuid4().hex}",
                                "content": f'For your clarification "{question}", my answer is: {visible_answer}',
                                "additional_kwargs": {
                                    "hide_from_ui": True,
                                    "human_input_response": response,
                                    "design_feedback_surface_id": surface_id,
                                },
                            }
                        ]
                    },
                    context={
                        "dbtl_supervisor_enabled": True,
                        "dbtl_explicit_choice": "continue_cycle",
                        "dbtl_selected_cycle_id": cycle_id,
                    },
                    on_disconnect="continue",
                ),
                body.originating_thread_id,
                request,
            )
            choice_label = str((option or {}).get("label") or answer) if body.action.kind == "chair_option" else answer
            await _post_design_meeting_turn(
                request,
                thread_id=body.originating_thread_id,
                run_id=record.run_id,
                surface_id=surface_id,
                design_round=int(surface.get("design_round") or 1),
                choice_label=choice_label,
                comment=written_feedback,
            )
            failed_attempts = receipt.get("failed_attempts")
            receipt = {
                "kind": body.action.kind,
                "run_id": record.run_id,
                "originating_thread_id": body.originating_thread_id,
                "message": "Recorded. The Design chair is resuming in the originating conversation.",
                **({"failed_attempts": failed_attempts} if isinstance(failed_attempts, list) else {}),
            }
            updated = await repo.update_stage_feedback_action(
                action_id,
                project_id=project_id,
                status="resume_started",
                run_id=record.run_id,
                receipt=receipt,
            )
            _feedback_event(
                "design_feedback.resume_started",
                surface_id=surface_id,
                project_id=project_id,
                cycle_id=cycle_id,
                thread_id=body.originating_thread_id,
                action_kind=body.action.kind,
                revision=body.expected_db_revision,
            )
            _watch_round_if_possible(
                request,
                user_id=user_id,
                thread_id=body.originating_thread_id,
                run_id=record.run_id,
                surface_id=surface_id,
                explanation=("The Design chair stopped before it could produce a follow-up deck. Reopen the existing deck to edit your answer and try again."),
                success_has_follow_up=round_has_follow_up,
            )
            return {**updated, "replayed": replayed}

        if body.action.kind == "convene_review_meeting":
            cycle_now = await repo.get_cycle(cycle_id, project_id=project_id)
            stage_row = next(
                (item for item in (cycle_now or {}).get("stages", []) if item.get("stage") == surface_stage),
                None,
            )
            gate = _surface_meeting_gate(surface, dbtl_config, stage=stage_row, cycle=cycle_now)
            # The read model already withheld this intent, but a client holds a
            # deck for as long as it likes: the meeting may have been convened,
            # completed, or turned off since the page was rendered.
            if gate is None or not gate.can_convene:
                raise DesignFeedbackConflict(f"A {surface_stage.title()} review meeting cannot be convened from this deck.")
            record = await start_run(
                RunCreateRequest(
                    input={
                        "messages": [
                            {
                                "role": "user",
                                # See ``_start_post_approval_handoff``: an explicit
                                # identity is what lets this run's own presented
                                # artifacts and cards reach durable chat history.
                                "id": f"dbtl-review-meeting__{uuid4().hex}",
                                "content": (f"Convene the {surface_stage.title()} review meeting for the recorded evidence." + (f"\n\n{written_feedback}" if written_feedback else "")),
                                "additional_kwargs": {
                                    "hide_from_ui": True,
                                    "dbtl_design_kickoff": True,
                                    "design_feedback_surface_id": surface_id,
                                },
                            }
                        ]
                    },
                    context={
                        "dbtl_supervisor_enabled": True,
                        "dbtl_explicit_choice": "continue_cycle",
                        "dbtl_selected_cycle_id": cycle_id,
                        # The stage is server-owned, taken from the surface the
                        # server registered rather than from the request: a deck
                        # that could name its own stage could convene a meeting
                        # over evidence it was never rendered from.
                        "dbtl_review_meeting_stage": surface_stage,
                    },
                    on_disconnect="continue",
                ),
                body.originating_thread_id,
                request,
            )
            # Same silence gap as the revision round: the meeting reports
            # through its own reply, so a run that dies must be announced.
            _watch_round_if_possible(
                request,
                user_id=user_id,
                thread_id=body.originating_thread_id,
                run_id=record.run_id,
                surface_id=surface_id,
                explanation=(f"The {surface_stage.title()} review meeting stopped before it could report. No meeting was recorded — you can convene it again from the stage's review page."),
                success_has_follow_up=round_has_follow_up,
            )
            receipt = {
                "kind": "convene_review_meeting",
                "run_id": record.run_id,
                "originating_thread_id": body.originating_thread_id,
                "message": f"The {surface_stage.title()} review meeting is starting in the originating conversation.",
            }
            updated = await repo.update_stage_feedback_action(
                action_id,
                project_id=project_id,
                status="resume_started",
                run_id=record.run_id,
                receipt=receipt,
            )
            _feedback_event(
                "design_feedback.review_meeting_convened",
                surface_id=surface_id,
                project_id=project_id,
                cycle_id=cycle_id,
                thread_id=body.originating_thread_id,
                action_kind=body.action.kind,
                revision=body.expected_db_revision,
            )
            return {**updated, "replayed": replayed}

        if body.action.kind == "retry_with_guidance":
            surface_gate = _surface_transition_gate(surface) or {}
            evidence_exception = surface_gate.get("evidence_exception")
            if not dbtl_config.degraded_evidence_continuation or surface_stage not in {"build", "test"} or not isinstance(evidence_exception, dict):
                raise DesignFeedbackConflict("This deck does not own an active evidence-exception retry.")
            if not written_feedback:
                raise DesignFeedbackConflict("Retry with guidance requires a comment for the next attempt.")
            record = await start_run(
                RunCreateRequest(
                    input={
                        "messages": [
                            {
                                "role": "user",
                                "id": f"dbtl-evidence-retry__{uuid4().hex}",
                                "content": f"Retry {surface_stage.title()} with human guidance. Show the server-owned retry control before dispatching work.\n\nGuidance: {written_feedback}",
                                "additional_kwargs": {
                                    "hide_from_ui": True,
                                    "design_feedback_surface_id": surface_id,
                                },
                            }
                        ]
                    },
                    context={
                        "dbtl_supervisor_enabled": True,
                        "dbtl_explicit_choice": "continue_cycle",
                        "dbtl_selected_cycle_id": cycle_id,
                    },
                    on_disconnect="continue",
                ),
                body.originating_thread_id,
                request,
                server_context={
                    "dbtl_evidence_retry": {
                        "cycle_id": cycle_id,
                        "cycle_revision": body.expected_db_revision,
                        "stage": surface_stage,
                        "dossier_hash": evidence_exception.get("content_hash"),
                        "reason_codes": list(evidence_exception.get("reason_codes") or []),
                        "available_artifacts": list(evidence_exception.get("available_artifacts") or []),
                        "initial_hint": written_feedback,
                    }
                },
            )
            receipt = {
                "kind": "retry_with_guidance",
                "run_id": record.run_id,
                "originating_thread_id": body.originating_thread_id,
                "message": f"The {surface_stage.title()} retry guidance is recorded. Confirm the server-owned retry control in chat to dispatch work.",
            }
            updated = await repo.update_stage_feedback_action(
                action_id,
                project_id=project_id,
                status="resume_started",
                run_id=record.run_id,
                receipt=receipt,
            )
            _watch_evidence_retry_delivery(
                request,
                repo=repo,
                user_id=user_id,
                project_id=project_id,
                thread_id=body.originating_thread_id,
                surface_id=surface_id,
                action_id=action_id,
                run_id=record.run_id,
                stage=surface_stage,
            )
            return {**updated, "replayed": replayed}

        binding = {
            "feedback_surface_id": surface_id,
            "deck_content_hash": surface["deck_content_hash"],
            "deck_schema_version": surface["deck_schema_version"],
            "evidence": expected_evidence,
        }
        workflow_key = f"design-deck:{action_id}"
        surface_transition_gate = _surface_transition_gate(surface)
        transition_gate = surface_transition_gate if dbtl_config.progressive_gate else None
        evidence_exception = dict((surface_transition_gate or {}).get("evidence_exception") or {})
        if surface_stage != "design" and body.action.kind in TRANSITION_INTENTS:
            current_cycle = await repo.get_cycle(cycle_id, project_id=project_id)
            current_stage = next(
                (item for item in (current_cycle or {}).get("stages", []) if item.get("stage") == surface_stage),
                None,
            )
            meeting_gate = _surface_meeting_gate(
                surface,
                dbtl_config,
                stage=current_stage,
                cycle=current_cycle,
            )
            if meeting_gate is not None and meeting_gate.transition_routes_locked:
                raise DesignFeedbackConflict(f"The {surface_stage.title()} gate requires its review meeting before this decision can be recorded.")
        if body.action.kind in {"advance", "park"} and transition_gate is None:
            raise DesignFeedbackConflict("This deck does not carry a progressive transition gate.")
        if body.action.kind in {"advance", "park"} and surface_stage != "design":
            raise DesignFeedbackConflict(f"The {body.action.kind.replace('_', ' ')} intent is not implemented for the {surface_stage.title()} gate.")
        if body.action.kind == "learn_exploratory":
            # Only Build is asked whether its result is worth qualifying, and
            # only a deployment that enabled the choice may record the answer.
            # The repository refuses this too; refusing here as well means the
            # deck is told why instead of getting a generic workflow error.
            if surface_stage != "build":
                raise DesignFeedbackConflict(f"Closing without retention qualification is a Build decision; the {surface_stage.title()} gate cannot take it.")
            if not conditional_test_enabled():
                raise DesignFeedbackConflict("This deployment requires retention qualification; a Build cannot close straight to Learn.")
        assessment = dict((transition_gate or {}).get("assessment") or {})
        assessed_difficulty = str(assessment.get("difficulty") or "")
        assessment_rationale = str(assessment.get("rationale") or "")
        human_override = body.action.difficulty_override
        effective_difficulty = human_override or assessed_difficulty
        offered_routes = [str(route.get("slug")) for route in (transition_gate or {}).get("routes", []) if isinstance(route, dict) and route.get("slug")]
        progressive_transition = (
            {
                "assessed_difficulty": assessed_difficulty,
                "assessment_rationale": assessment_rationale,
                "human_override": human_override,
                "offered_routes": offered_routes,
            }
            if transition_gate is not None
            else None
        )
        if body.action.kind == "advance":
            if effective_difficulty == "high_stakes" and not written_feedback:
                raise DesignFeedbackConflict("A high-stakes approval requires the reviewer's written rationale.")
            if not _route_available(transition_gate or {}, "advance"):
                raise DesignFeedbackConflict("Continue to Build is currently blocked.")
            cycle = await repo.review_stage(
                cycle_id=cycle_id,
                project_id=project_id,
                stage="design",
                decision="approve",
                rationale=written_feedback or "Approved through the one-action progressive gate.",
                expected_db_revision=body.expected_db_revision,
                reviewer_user_id=user_id,
                reviewer_project_role=str(project["current_user_role"]),
                idempotency_key=workflow_key,
                design_feedback_provenance={
                    "input_source": "design_deck",
                    "feedback_surface_id": surface_id,
                    "deck_content_hash": surface["deck_content_hash"],
                    "deck_schema_version": surface["deck_schema_version"],
                    "selected_action": "advance",
                    "selected_card_ids": [],
                    "human_comment": body.comment.strip() or None,
                    "slide_comments": recorded_slide_comments,
                    "active_slide_id": active_slide_id,
                    "rationale_projection": written_feedback or "Approved through the one-action progressive gate.",
                    "rationale_source": "human" if written_feedback else "server_projection",
                },
                progressive_transition=progressive_transition,
                auto_submit=True,
            )
            updated, _handoff = await finish_approval_handoff(
                cycle,
                approved_stage="design",
                receipt={
                    "kind": "advance",
                    "db_revision": cycle["db_revision"],
                    "assessed_difficulty": assessed_difficulty,
                    "human_override": human_override,
                },
            )
            return {**updated, "cycle": cycle, "replayed": replayed}
        if body.action.kind == "park":
            if not _route_available(transition_gate or {}, "park"):
                raise DesignFeedbackConflict("Park is not a legal route from this gate.")
            cycle = await repo.park_cycle(
                cycle_id=cycle_id,
                project_id=project_id,
                stage="design",
                expected_db_revision=body.expected_db_revision,
                actor_user_id=user_id,
                idempotency_key=workflow_key,
                decision_surface_id=surface_id,
                assessed_difficulty=assessed_difficulty,
                assessment_rationale=assessment_rationale,
                human_override=human_override,
                offered_routes=offered_routes,
            )
            receipt = {
                "kind": "park",
                "db_revision": cycle["db_revision"],
                "message": "Cycle parked. Ordinary cycle-scoped requests now go to the lead agent with this Design marked unapproved.",
                "assessed_difficulty": assessed_difficulty,
                "human_override": human_override,
            }
            updated = await repo.update_stage_feedback_action(
                action_id,
                project_id=project_id,
                status="accepted",
                receipt=receipt,
            )
            return {**updated, "cycle": cycle, "replayed": replayed}
        if body.action.kind == "submit_for_review":
            cycle = await repo.submit_stage_for_review(
                cycle_id=cycle_id,
                project_id=project_id,
                stage=surface_stage,
                expected_db_revision=body.expected_db_revision,
                actor_user_id=user_id,
                idempotency_key=workflow_key,
                design_feedback_binding=binding,
            )
            receipt = {
                "kind": "submit_for_review",
                "db_revision": cycle["db_revision"],
                "message": f"The {surface_stage.title()} package is submitted for human review.",
                "assessed_difficulty": assessed_difficulty or None,
                "human_override": human_override,
            }
            updated = await repo.update_stage_feedback_action(
                action_id,
                project_id=project_id,
                status="accepted",
                receipt=receipt,
            )
            _feedback_event(
                "design_feedback.accepted",
                surface_id=surface_id,
                project_id=project_id,
                cycle_id=cycle_id,
                thread_id=body.originating_thread_id,
                action_kind=body.action.kind,
                revision=int(cycle["db_revision"]),
            )
            return {**updated, "cycle": cycle, "replayed": replayed}

        if body.action.kind == "continue_with_red_flag":
            if not dbtl_config.degraded_evidence_continuation or surface_stage not in {"build", "test"} or not evidence_exception:
                raise DesignFeedbackConflict("This deck does not own an active evidence exception.")
            if not written_feedback:
                raise DesignFeedbackConflict("Continue with red flag requires the reviewer's written rationale.")
            if surface_stage == "test":
                service = TestReviewService(
                    repo=repo,
                    app_config=config,
                    runtime_reader=lambda _config: {
                        "user_id": user_id,
                        "project_role": str(project["current_user_role"]),
                    },
                )
                snapshot = await service.snapshot(project_id=project_id, cycle_id=cycle_id)
                if snapshot is None or str(dict(snapshot.get("evaluation") or {}).get("outcome") or "") != "invalidated":
                    raise DesignFeedbackConflict("Test does not have a current server-computed invalidated assessment.")
                recorded = await service.record_outcome(
                    project_id=project_id,
                    cycle_id=cycle_id,
                    snapshot=snapshot,
                    recommendation="learn_from_invalidated_evidence",
                    config={},
                    idempotency_key=workflow_key,
                    human_rationale=written_feedback,
                    review_provenance={
                        "input_source": "stage_deck",
                        "feedback_surface_id": surface_id,
                        "deck_content_hash": surface["deck_content_hash"],
                        "deck_schema_version": surface["deck_schema_version"],
                        "selected_action": body.action.kind,
                        "human_comment": body.comment.strip(),
                        "rationale_projection": written_feedback,
                        "rationale_source": "human",
                    },
                )
                cycle = dict(recorded.get("cycle") or {})
            else:
                current = await repo.get_cycle(cycle_id, project_id=project_id)
                current_status = next((str(item.get("status") or "") for item in (current or {}).get("stages", []) if item.get("stage") == surface_stage), "")
                cycle = await repo.review_stage(
                    cycle_id=cycle_id,
                    project_id=project_id,
                    stage=surface_stage,
                    decision="advanced_with_exception",
                    rationale=written_feedback,
                    expected_db_revision=body.expected_db_revision,
                    reviewer_user_id=user_id,
                    reviewer_project_role=str(project["current_user_role"]),
                    idempotency_key=workflow_key,
                    design_feedback_provenance={
                        "input_source": "stage_deck",
                        "feedback_surface_id": surface_id,
                        "deck_content_hash": surface["deck_content_hash"],
                        "deck_schema_version": surface["deck_schema_version"],
                        "selected_action": body.action.kind,
                        "human_comment": body.comment.strip(),
                        "slide_comments": recorded_slide_comments,
                        "active_slide_id": active_slide_id,
                        "rationale_projection": written_feedback,
                        "rationale_source": "human",
                        "evidence_exception": evidence_exception,
                    },
                    progressive_transition={
                        "assessed_difficulty": "exception",
                        "assessment_rationale": written_feedback,
                        "offered_routes": list(evidence_exception.get("recovery_options") or []),
                    },
                    auto_submit=current_status in {"in_progress", "changes_requested"},
                )
            updated, _handoff = await finish_approval_handoff(
                cycle,
                approved_stage=surface_stage,
                receipt={
                    "kind": "continue_with_red_flag",
                    "db_revision": cycle["db_revision"],
                    "evidence_exception_hash": evidence_exception.get("content_hash"),
                },
            )
            return {**updated, "cycle": cycle, "replayed": replayed}

        if transition_gate is not None and human_override is None:
            prior_actions = await repo.stage_feedback_actions(surface_id, project_id=project_id)
            prior_submit = next(
                (item for item in reversed(prior_actions) if item.get("action_group") == "stage_submit" and isinstance(item.get("receipt"), dict)),
                None,
            )
            prior_receipt = dict((prior_submit or {}).get("receipt") or {})
            prior_override = prior_receipt.get("human_override")
            if prior_override in _TRANSITION_DIFFICULTIES:
                human_override = str(prior_override)
                effective_difficulty = human_override
                progressive_transition = {
                    **(progressive_transition or {}),
                    "human_override": human_override,
                }
        if body.action.kind in {
            "recommend_promotion",
            "close_without_candidate",
        }:
            raise DesignFeedbackConflict(f"The {body.action.kind.replace('_', ' ')} intent requires its stage-specific review record.")
        if body.action.kind == "choose_route":
            if surface_stage != "test" or len(body.action.option_ids) != 1:
                raise DesignFeedbackConflict("A Test route decision must name exactly one server-offered route.")
            service = TestReviewService(
                repo=repo,
                app_config=config,
                runtime_reader=lambda _config: {
                    "user_id": user_id,
                    "project_role": str(project["current_user_role"]),
                },
            )
            snapshot = await service.snapshot(project_id=project_id, cycle_id=cycle_id)
            if snapshot is None:
                raise DesignFeedbackConflict("Test no longer has complete reviewable evidence.")
            recommendation = body.action.option_ids[0]
            allowed = {str(item) for item in dict(snapshot.get("evaluation") or {}).get("allowed_recommendations", [])}
            if recommendation not in allowed:
                raise DesignFeedbackConflict("That route is not compatible with the server-computed Test outcome.")
            recorded = await service.record_outcome(
                project_id=project_id,
                cycle_id=cycle_id,
                snapshot=snapshot,
                recommendation=recommendation,
                config={},
                idempotency_key=workflow_key,
                human_rationale=written_feedback,
                review_provenance={
                    "input_source": "stage_deck",
                    "feedback_surface_id": surface_id,
                    "deck_content_hash": surface["deck_content_hash"],
                    "deck_schema_version": surface["deck_schema_version"],
                    "selected_action": body.action.kind,
                    "human_comment": body.comment.strip(),
                    "rationale_projection": written_feedback,
                    "rationale_source": "human" if written_feedback else "server",
                },
            )
            cycle = dict(recorded.get("cycle") or {})
            receipt = {
                "kind": "choose_route",
                "route": recommendation,
                "db_revision": cycle.get("db_revision"),
                "message": f"Test outcome recorded. Route: {recommendation.replace('_', ' ')}.",
            }
            if recommendation == "advance_to_learn" and cycle.get("state") == "learn":
                updated, _handoff = await finish_approval_handoff(
                    cycle,
                    approved_stage="test",
                    receipt=receipt,
                )
                return {**updated, "cycle": cycle, "replayed": replayed}
            updated = await repo.update_stage_feedback_action(
                action_id,
                project_id=project_id,
                status="review_recorded",
                receipt=receipt,
            )
            return {**updated, "cycle": cycle, "replayed": replayed}
        if effective_difficulty == "high_stakes" and not written_feedback:
            raise DesignFeedbackConflict(f"A high-stakes {surface_stage.title()} verdict requires the reviewer's written rationale.")
        if body.action.kind == "reject" and not written_feedback:
            raise DesignFeedbackConflict(f"{body.action.kind.replace('_', ' ').title()} requires a comment.")
        if body.action.kind == "learn_exploratory":
            default_rationale = "Approved from the registered Build feedback deck and closed without retention qualification."
        elif body.action.kind == "approve":
            default_rationale = f"Approved from the registered {surface_stage.title()} feedback deck."
        else:
            default_rationale = f"Selected contested {surface_stage.title()} issues require refinement."
        rationale = written_feedback or default_rationale
        rationale_projection = rationale
        if body.action.kind == "request_changes":
            target = f"the recorded issues {', '.join(body.action.option_ids)}" if body.action.option_ids else f"the {surface_stage.title()} evidence described in the reviewer's comment"
            rationale_projection = f"Requested changes to {target}.\n\n{rationale}"
        provenance = {
            "input_source": "design_deck" if surface_stage == "design" else "stage_deck",
            "feedback_surface_id": surface_id,
            "deck_content_hash": surface["deck_content_hash"],
            "deck_schema_version": surface["deck_schema_version"],
            "selected_action": body.action.kind,
            "selected_card_ids": body.action.option_ids,
            "human_comment": body.comment.strip() or None,
            "slide_comments": recorded_slide_comments,
            "active_slide_id": active_slide_id,
            "rationale_projection": rationale_projection,
            "rationale_source": "human" if written_feedback else "server_projection",
        }
        auto_submit = False
        if body.action.kind in {"approve", "learn_exploratory", "request_changes"} and transition_gate is not None:
            # The simple gate card records a verdict in one action while the
            # stage is still open, materializing the submit and the verdict in
            # one review — the same shape the routine `advance` route uses.
            # Approve arrives here rather than through `advance` when the Build
            # edge is blocked: the Design verdict is still legal, and giving it
            # is what opens the data work that unblocks the edge.
            current = await repo.get_cycle(cycle_id, project_id=project_id)
            current_status = next(
                (str(item.get("status") or "") for item in (current or {}).get("stages", []) if item.get("stage") == surface_stage),
                "",
            )
            auto_submit = current_status in {"in_progress", "changes_requested"}
        cycle = await repo.review_stage(
            cycle_id=cycle_id,
            project_id=project_id,
            stage=surface_stage,
            # An exploratory closeout is still an approval of the Build; what
            # differs is what the reviewer decided it was for, which travels as
            # the disposition rather than as a fourth verdict.
            decision="approve" if body.action.kind == "learn_exploratory" else body.action.kind,
            rationale=rationale_projection,
            expected_db_revision=body.expected_db_revision,
            reviewer_user_id=user_id,
            reviewer_project_role=str(project["current_user_role"]),
            idempotency_key=workflow_key,
            design_feedback_provenance=provenance,
            progressive_transition=progressive_transition,
            auto_submit=auto_submit,
            build_disposition=("learn_exploratory" if body.action.kind == "learn_exploratory" else None),
        )
        handoff: _PostApprovalHandoff | None = None
        handoff_updated: dict[str, Any] | None = None
        if body.action.kind in {"approve", "learn_exploratory"}:
            handoff_updated, handoff = await finish_approval_handoff(
                cycle,
                approved_stage=surface_stage,
                receipt={
                    "kind": body.action.kind,
                    "db_revision": cycle["db_revision"],
                    "human_override": human_override,
                },
            )
        refinement_run_id: str | None = None
        if body.action.kind == "request_changes" and surface_stage == "design":
            refinement_target = f"these recorded issues: {', '.join(body.action.option_ids)}" if body.action.option_ids else "the reviewer's written objection"
            refinement_request = f"Refine the approved Design candidate for {refinement_target}.\n\n{rationale}"
            record = await start_run(
                RunCreateRequest(
                    input={
                        "messages": [
                            {
                                "role": "user",
                                "content": refinement_request,
                                "additional_kwargs": {
                                    "hide_from_ui": True,
                                    "dbtl_design_kickoff": True,
                                    "design_feedback_surface_id": surface_id,
                                },
                            }
                        ]
                    },
                    context={
                        "dbtl_supervisor_enabled": True,
                        "dbtl_explicit_choice": "continue_cycle",
                        "dbtl_selected_cycle_id": cycle_id,
                    },
                    on_disconnect="continue",
                ),
                body.originating_thread_id,
                request,
            )
            refinement_run_id = record.run_id
            # The round's explanation rides on its own reply, so a run that
            # dies mid-flight says nothing in chat. The watcher speaks only on
            # a terminal failure; a finished round explains itself.
            _watch_round_if_possible(
                request,
                user_id=user_id,
                thread_id=body.originating_thread_id,
                run_id=refinement_run_id,
                surface_id=surface_id,
                explanation=("The Design revision round stopped before it could reply. Your “Request changes” verdict is recorded and nothing was lost — say “run the meeting again” in this conversation to retry the round."),
                success_has_follow_up=round_has_follow_up,
            )
        receipt = {
            "kind": body.action.kind,
            "db_revision": cycle["db_revision"],
            "message": (
                "Design changes were recorded and a focused refinement started."
                if refinement_run_id
                else (
                    f"{surface_stage.title()} approval recorded. Choose the next governed action in the originating conversation."
                    if handoff is not None and handoff.status == "started"
                    else f"{surface_stage.title()} review recorded: {body.action.kind.replace('_', ' ')}."
                )
            ),
        }
        if refinement_run_id:
            receipt["run_id"] = refinement_run_id
            receipt["originating_thread_id"] = body.originating_thread_id
        elif handoff is not None and handoff.run_id:
            receipt["handoff_run_id"] = handoff.run_id
            receipt["originating_thread_id"] = body.originating_thread_id
        if handoff_updated is not None:
            _feedback_event(
                "design_feedback.review_recorded",
                surface_id=surface_id,
                project_id=project_id,
                cycle_id=cycle_id,
                thread_id=body.originating_thread_id,
                action_kind=body.action.kind,
                revision=int(cycle["db_revision"]),
            )
            return {**handoff_updated, "cycle": cycle, "replayed": replayed}
        updated = await repo.update_stage_feedback_action(
            action_id,
            project_id=project_id,
            status="review_recorded",
            run_id=refinement_run_id,
            receipt=receipt,
        )
        _feedback_event(
            "design_feedback.review_recorded",
            surface_id=surface_id,
            project_id=project_id,
            cycle_id=cycle_id,
            thread_id=body.originating_thread_id,
            action_kind=body.action.kind,
            revision=int(cycle["db_revision"]),
        )
        return {**updated, "cycle": cycle, "replayed": replayed}
    except HTTPException as exc:
        await repo.update_stage_feedback_action(
            action_id,
            project_id=project_id,
            status="failed",
            failure_code=f"http_{exc.status_code}",
        )
        raise
    except Exception as exc:  # noqa: BLE001
        await repo.update_stage_feedback_action(
            action_id,
            project_id=project_id,
            status="failed",
            failure_code=type(exc).__name__[:64],
        )
        _feedback_event(
            "design_feedback.resume_failed" if body.action.kind.startswith("chair_") else "design_feedback.conflict",
            surface_id=surface_id,
            project_id=project_id,
            cycle_id=cycle_id,
            thread_id=body.originating_thread_id,
            action_kind=body.action.kind,
            revision=body.expected_db_revision,
            failure_code=type(exc).__name__[:64],
        )
        raise _translate(exc) from exc


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
    if body.originating_thread_id is not None:
        origin = await get_thread_store(request).get(
            body.originating_thread_id,
            user_id=user_id,
        )
        if not origin or origin.get("project_id") != project_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The originating conversation is not available in this project.",
            )
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
            policy_version=DBTL_POLICY_VERSION,
            idempotency_key=body.idempotency_key,
            parent_cycle_id=body.parent_cycle_id,
            originating_thread_id=(str(body.originating_thread_id) if body.originating_thread_id is not None else None),
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
    # A rejection still requires the reviewer's own words — it ends the attempt,
    # and "rejected" with a generated sentence tells the next reader nothing.
    # The other two verdicts may take a labelled server projection, exactly as
    # the registered deck path already does.
    if body.decision == "reject" and not body.rationale:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Rejecting a stage requires a rationale.")
    rationale = body.rationale or _DEFAULT_REVIEW_RATIONALE[body.decision]
    try:
        return await repo.review_stage(
            cycle_id=cycle_id,
            project_id=project_id,
            stage=stage,
            decision=body.decision,
            rationale=rationale,
            expected_db_revision=body.expected_db_revision,
            reviewer_user_id=user_id,
            reviewer_project_role=str(project["current_user_role"]),
            idempotency_key=body.idempotency_key,
            design_feedback_provenance={
                "input_source": "design_sheet",
                "human_comment": body.rationale or None,
                "rationale_projection": rationale,
                # Says whether the recorded reasoning is the reviewer's or the
                # server's, so a later reader is never misled about who wrote it.
                "rationale_source": "human" if body.rationale else "server_projection",
            },
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
        "learn_from_invalidated_evidence",
        "repeat_test",
        "return_to_build",
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


@router.get("/projects/{project_id}/dbtl/cycles/{cycle_id}/stages/{stage}/workflow")
@require_permission("threads", "read")
async def get_stage_workflow(
    project_id: str,
    cycle_id: str,
    stage: StageName,
    request: Request,
    repo=Depends(get_dbtl_cycle_repo),
):
    """The Build workflow's ordered steps and what is still valid.

    Served in every mode, including when `dbtl.build_workflow_steps` is off:
    the flag governs whether the workflow *drives* execution, and a read model
    that disappeared with it could not tell an owner why their Build looks the
    way it does. It returns bounded metadata only — status, digests, ids, and
    timestamps — never raw prompts, secrets, or shell logs. Detailed task steps
    continue to come from the authenticated run-events endpoint by
    `(thread_id, run_id, task_id)`.
    """
    await _require_project(project_id, request)
    cycle = await repo.get_cycle(cycle_id, project_id=project_id)
    if cycle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found.")
    attempts = [entry for entry in cycle.get("stages", []) if entry.get("stage") == stage]
    if not attempts:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stage not found for this cycle.")
    stage_attempt_id = str(max(attempts, key=lambda entry: int(entry.get("attempt_number") or 0))["id"])
    view = await repo.build_workflow_view(project_id=project_id, stage_attempt_id=stage_attempt_id)
    return {"cycle_id": cycle_id, "stage": stage, **view}


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
    _require_knowledge_authority(project)
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
    _require_knowledge_authority(project)
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
    _require_knowledge_authority(project)
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
