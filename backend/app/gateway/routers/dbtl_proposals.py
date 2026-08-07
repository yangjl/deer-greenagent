"""Classifier shadow evaluation and its human-outcome telemetry.

Three endpoints, and the boundary between them is the phase's whole point:

``POST .../evaluate``
    Runs deterministic-first routing over one request and records what it
    concluded. It **creates no DBTL record** — it cannot, because it holds a
    telemetry repository and nothing else. Visible setup interactions are
    emitted by the supervisor as native Human Input Cards.

``POST .../{evaluation_id}/outcome``
    Attaches what the human did in the native setup interaction.

``GET .../evaluations``
    The internal evaluation drawer. Admin-only, since it is a review surface
    for people calibrating thresholds rather than a product feature.

Creating an actual cycle stays behind the supervisor's confirmation. Nothing
here can shortcut it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.gateway.authz import require_permission
from app.gateway.deps import (
    get_classifier_evaluation_repo,
    get_config,
    get_dbtl_discovery_repo,
    get_workspace_repo,
    require_admin_user,
)
from deerflow.config.app_config import AppConfig
from deerflow.dbtl.policy import DBTL_POLICY_VERSION
from deerflow.dbtl.routing import ExplicitChoice, RouteKind, RoutingRequest, route_request
from deerflow.persistence.telemetry import ClassifierEvaluationConflict

router = APIRouter(prefix="/api", tags=["dbtl-proposals"])
logger = logging.getLogger(__name__)

MAX_REQUEST_TEXT = 20_000
DEFAULT_DRAWER_LIMIT = 100


class EvaluateRequest(BaseModel):
    """One request to classify. ``extra="forbid"`` so a client cannot smuggle
    a decision, a confidence, or a record flag into a shadow evaluation."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_REQUEST_TEXT)
    thread_id: str | None = Field(default=None, max_length=128)
    selected_cycle_id: str | None = Field(default=None, max_length=64)
    explicit_choice: Literal["ordinary", "start_cycle", "continue_cycle"] | None = None
    # Telemetry mirrors the supervisor's checkpoint-derived first-turn prior.
    # This field cannot create a record and no longer mounts a client card.
    is_new_conversation: bool = False
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("text")
    @classmethod
    def text_must_have_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain text")
        return value


class OutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal[
        "start_setup",
        "keep_ordinary",
        "not_sure",
        "dismissed",
        "continue_cycle",
    ]


async def _require_project(project_id: str, request: Request) -> tuple[dict, str]:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    user_id = str(user.id)
    project = await get_workspace_repo(request).get_project(project_id, user_id=user_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project, user_id


def _dbtl_config(request: Request, config: AppConfig):
    return getattr(request.app.state, "dbtl_config_override", config.dbtl)


def _evaluation_id(project_id: str, user_id: str, idempotency_key: str) -> str:
    """A stable id from the caller's key, scoped so keys cannot collide across
    projects or users."""
    digest = hashlib.sha256(f"{project_id}\x00{user_id}\x00{idempotency_key}".encode()).hexdigest()
    return f"eval_{digest[:32]}"


def _request_fingerprint(body: EvaluateRequest) -> str:
    """Bind an idempotency key without retaining the raw request separately."""
    canonical = json.dumps(
        {
            "text": body.text,
            "thread_id": body.thread_id,
            "selected_cycle_id": body.selected_cycle_id,
            "explicit_choice": body.explicit_choice,
            "is_new_conversation": body.is_new_conversation,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@router.post("/projects/{project_id}/dbtl/proposals/evaluate")
@require_permission("threads", "write")
async def evaluate_request(
    project_id: str,
    body: EvaluateRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    repo=Depends(get_classifier_evaluation_repo),
):
    """Classify one request in shadow mode. Creates no DBTL record, ever."""
    _project, user_id = await _require_project(project_id, request)
    dbtl_config = _dbtl_config(request, config)
    project_cycle_count: int | None = None
    has_unfinished_cycles: bool | None = None
    cycle_repo = getattr(request.app.state, "dbtl_cycle_repo", None)
    if cycle_repo is not None:
        try:
            summary = await cycle_repo.project_cycle_summary(project_id)
            raw_count = summary.get("project_cycle_count")
            raw_unfinished = summary.get("has_unfinished_cycles")
            if isinstance(raw_count, int) and raw_count >= 0:
                project_cycle_count = raw_count
            if isinstance(raw_unfinished, bool):
                has_unfinished_cycles = raw_unfinished
        except Exception:
            logger.warning(
                "Failed to resolve DBTL lifecycle context for proposal evaluation in project %s",
                project_id,
                exc_info=True,
            )

    active_discovery_id: str | None = None
    discovery_suppressed = False
    discovery_repo = getattr(request.app.state, "dbtl_discovery_repo", None)
    if discovery_repo is not None and dbtl_config.conversational_discovery_enabled and body.thread_id:
        try:
            latest = await discovery_repo.get_latest(
                project_id=project_id,
                thread_id=body.thread_id,
                user_id=user_id,
            )
            if latest and latest.get("status") in {"gathering", "ready", "offered"}:
                active_discovery_id = str(latest["id"])
            elif latest and latest.get("status") == "declined":
                discovery_suppressed = True
        except Exception:
            # Evaluation remains observation. The runtime owns the final route
            # and will fail closed if its durable discovery state is unreadable.
            logger.warning(
                "Failed to resolve discovery routing state for proposal evaluation in project %s",
                project_id,
                exc_info=True,
            )

    decision = route_request(
        RoutingRequest(
            text=body.text,
            project_id=project_id,
            selected_cycle_id=body.selected_cycle_id,
            explicit_choice=ExplicitChoice(body.explicit_choice) if body.explicit_choice else None,
            is_new_conversation=body.is_new_conversation,
            project_cycle_count=project_cycle_count,
            has_unfinished_cycles=has_unfinished_cycles,
            discovery_enabled=dbtl_config.conversational_discovery_enabled,
            discovery_classifier_entry=dbtl_config.discovery_classifier_entry,
            active_discovery_id=active_discovery_id,
            discovery_suppressed=discovery_suppressed,
        )
    )
    evaluation_id = _evaluation_id(project_id, user_id, body.idempotency_key)
    classifier = decision.classifier
    try:
        await repo.record_evaluation(
            evaluation_id=evaluation_id,
            project_id=project_id,
            thread_id=body.thread_id,
            user_id=user_id,
            route_kind=str(decision.kind),
            route_source=str(decision.source),
            request_fingerprint=_request_fingerprint(body),
            band=str(classifier.band) if classifier else "low",
            confidence=classifier.confidence if classifier else 0.0,
            rule_hits=[{"rule_id": hit.rule_id, "weight": hit.weight, "evidence": hit.evidence} for hit in (classifier.rule_hits if classifier else ())],
            missing_fields=list(classifier.missing_fields) if classifier else [],
            proposed_objective=classifier.proposed_objective if classifier else "",
            policy_version=DBTL_POLICY_VERSION,
        )
    except ClassifierEvaluationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        # Telemetry is observation. Losing a row must not break the user's
        # request — that would let a measurement surface degrade the
        # product it is measuring.
        logger.warning("Failed to record DBTL classifier evaluation for project %s", project_id, exc_info=True)

    return {
        "evaluation_id": evaluation_id,
        "route_kind": str(decision.kind),
        "route_source": str(decision.source),
    }


@router.post("/projects/{project_id}/dbtl/proposals/{evaluation_id}/outcome")
@require_permission("threads", "write")
async def record_outcome(
    project_id: str,
    evaluation_id: str,
    body: OutcomeRequest,
    request: Request,
    repo=Depends(get_classifier_evaluation_repo),
):
    """Record what the human did with a card."""
    _project, user_id = await _require_project(project_id, request)
    stored = await repo.record_outcome(
        evaluation_id=evaluation_id,
        project_id=project_id,
        user_id=user_id,
        outcome=body.outcome,
    )
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evaluation not found")
    return stored


@router.get("/projects/{project_id}/dbtl/proposals/evaluations")
@require_permission("threads", "read")
async def list_evaluations(
    project_id: str,
    request: Request,
    limit: int = DEFAULT_DRAWER_LIMIT,
    repo=Depends(get_classifier_evaluation_repo),
):
    """The internal evaluation drawer: shadow decisions and their outcomes."""
    await _require_project(project_id, request)
    await require_admin_user(request, detail="DBTL classifier evaluations are available to administrators only.")
    discovery_repo = getattr(request.app.state, "dbtl_discovery_repo", None)
    discoveries = await discovery_repo.list_project_outcomes(project_id, limit=limit) if discovery_repo is not None else []
    discovery_stats = await discovery_repo.project_outcome_stats(project_id) if discovery_repo is not None else {"total": 0, "classifier_entries": 0, "confirmed": 0, "declined": 0, "active": 0}
    return {
        "project_id": project_id,
        "evaluations": await repo.list_evaluations(project_id, limit=limit),
        "stats": await repo.evaluation_stats(project_id),
        "discoveries": discoveries,
        "discovery_stats": discovery_stats,
    }


@router.get("/projects/{project_id}/dbtl/discovery/status")
@require_permission("threads", "read")
async def discovery_status(
    project_id: str,
    request: Request,
    thread_id: str = Query(min_length=1, max_length=128),
    config: AppConfig = Depends(get_config),
    repo=Depends(get_dbtl_discovery_repo),
):
    """Server-derived composer state for one authorized project thread."""

    _project, user_id = await _require_project(project_id, request)
    dbtl_config = _dbtl_config(request, config)
    if not dbtl_config.conversational_discovery_enabled:
        return {"enabled": False, "discovery": None}
    active = await repo.get_active(
        project_id=project_id,
        thread_id=thread_id,
        user_id=user_id,
    )
    if active is None:
        return {"enabled": True, "discovery": None}
    return {
        "enabled": True,
        "discovery": {
            "id": active["id"],
            "status": active["status"],
            "trigger": active["trigger"],
            "revision": active["revision"],
            "turn_count": int((active.get("draft") or {}).get("turn_count") or 0),
            "updated_at": active["updated_at"],
        },
    }


__all__ = ["RouteKind", "router"]
