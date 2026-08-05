"""Classifier shadow evaluation and the DBTL Upgrade Proposal (Phase 4).

Three endpoints, and the boundary between them is the phase's whole point:

``POST .../evaluate``
    Runs deterministic-first routing over one request and records what it
    concluded. It **creates no DBTL record** — it cannot, because it holds a
    telemetry repository and nothing else. Whether the caller is shown a card
    is a separate question answered by ``dbtl.proposals_visible``.

``POST .../{evaluation_id}/outcome``
    Attaches what the human did. Dismissal is recorded like any other choice,
    because a card that gets ignored is exactly the false-upgrade signal.

``GET .../evaluations``
    The internal evaluation drawer. Admin-only, since it is a review surface
    for people calibrating thresholds rather than a product feature.

Creating an actual cycle stays where it already was: the Phase 3 endpoint,
behind its own confirmation. Nothing here can shortcut it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.gateway.authz import is_model_use_authorized, require_permission
from app.gateway.deps import (
    get_classifier_evaluation_repo,
    get_config,
    get_dbtl_discovery_repo,
    get_workspace_repo,
    require_admin_user,
)
from deerflow.config.app_config import AppConfig
from deerflow.dbtl.proposal import (
    CONFIRMATION_REQUIRED_NOTICE,
    RECORD_EFFECT,
    REQUIRED_GATES,
    ProposalOutcome,
    UpgradeProposal,
    build_proposal,
)
from deerflow.dbtl.routing import ExplicitChoice, RouteKind, RoutingRequest, route_request
from deerflow.dbtl.setup_draft import SetupDraft, build_draft_prompt, empty_draft, parse_draft_response
from deerflow.models import create_chat_model
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


class DraftSetupRequest(BaseModel):
    """A request to pre-fill the setup form.

    ``fields`` is echoed from the proposal the client is showing rather than
    chosen by the client freely: the server drops anything outside it when
    parsing, so a client cannot widen the record's shape through this call.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_REQUEST_TEXT)
    fields: list[str] = Field(default_factory=list, max_length=24)

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


def _serialize_proposal(proposal: UpgradeProposal) -> dict:
    # The cycle class is the user's choice on the card, so it is not decided
    # here; only the class-independent parts of the confirmation are sent.
    return {
        "kind": str(proposal.kind),
        "proposed_objective": proposal.proposed_objective,
        "missing_fields": list(proposal.missing_fields),
        "band": str(proposal.band),
        "confidence": proposal.confidence,
        "project_name": proposal.project_name,
        "cycle_id": proposal.cycle_id,
        # Constants, echoed so the client renders the reviewed wording rather
        # than its own paraphrase of it.
        "creates_record": proposal.creates_record,
        "requires_confirmation": proposal.requires_confirmation,
        "notice": proposal.notice,
        "confirmation": {
            "project_name": proposal.project_name,
            "required_gates": list(REQUIRED_GATES),
            "record_effect": RECORD_EFFECT,
            "notice": CONFIRMATION_REQUIRED_NOTICE,
        },
    }


def _serialize_draft(draft: SetupDraft, *, enabled: bool) -> dict:
    """The wire shape.

    ``assumed`` travels as its own list rather than being folded into the values
    so the UI cannot lose the distinction between what the scientist said and
    what the model proposed.
    """
    return {
        "enabled": enabled,
        "title": draft.title,
        "fields": dict(draft.fields),
        "assumed_fields": sorted(draft.assumed),
    }


def _response_text(response: object) -> str:
    """Pull plain text out of a chat response, tolerating block content."""
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Reasoning models interleave thinking blocks; only text blocks count.
        parts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
        return "\n".join(part for part in parts if part)
    return ""


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
    project, user_id = await _require_project(project_id, request)
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
    proposal = build_proposal(decision, project_name=str(project.get("name") or ""))

    evaluation_id = _evaluation_id(project_id, user_id, body.idempotency_key)
    if dbtl_config.classifier_shadow_enabled:
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
                proposed_objective=proposal.proposed_objective if proposal else "",
                policy_version=dbtl_config.policy_version,
            )
        except ClassifierEvaluationConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except Exception:
            # Telemetry is observation. Losing a row must not break the user's
            # request — that would let a measurement surface degrade the
            # product it is measuring.
            logger.warning("Failed to record DBTL classifier evaluation for project %s", project_id, exc_info=True)

    # Routing is reported honestly even when the card is hidden, so the drawer
    # and the stored row agree; visibility only governs what the user sees.
    proposals_visible = bool(dbtl_config.proposals_enabled)
    return {
        "evaluation_id": evaluation_id,
        "route_kind": str(decision.kind),
        "route_source": str(decision.source),
        "proposals_visible": proposals_visible,
        "proposal": _serialize_proposal(proposal) if (proposal and proposals_visible) else None,
    }


@router.post("/projects/{project_id}/dbtl/proposals/draft-setup")
@require_permission("threads", "write")
async def draft_setup(
    project_id: str,
    body: DraftSetupRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
):
    """Pre-fill the setup form from the user's request. Creates no record.

    Deliberately a separate call from ``evaluate``: evaluation runs beside every
    message the user sends, so putting a model round trip there would tax every
    turn. Drafting is needed only once, when the setup step actually opens.

    Every failure path returns an empty draft rather than an error. The setup
    form must remain usable when the model is slow, misconfigured, or down —
    degrading to the blank form users had before drafting existed.
    """
    project, _user_id = await _require_project(project_id, request)
    dbtl_config = _dbtl_config(request, config)

    fields = [f for f in (body.fields or []) if isinstance(f, str) and f.strip()]
    if not dbtl_config.setup_draft_enabled or not body.text.strip() or not fields:
        return _serialize_draft(empty_draft(), enabled=dbtl_config.setup_draft_enabled)

    # ``model:use`` is enforced for the drafting model like any other model
    # invocation the caller triggers; a denial degrades to the blank form the
    # setup step had before drafting existed rather than erroring.
    if not await is_model_use_authorized(request, dbtl_config.setup_draft_model_name):
        logger.warning(
            "DBTL setup drafting model %r is not authorized for this caller; returning a blank form",
            dbtl_config.setup_draft_model_name,
        )
        return _serialize_draft(empty_draft(), enabled=True)

    prompt = build_draft_prompt(
        request_text=body.text,
        project_name=str(project.get("name") or ""),
        fields=fields,
    )
    try:
        model = create_chat_model(
            name=dbtl_config.setup_draft_model_name,
            thinking_enabled=False,
            attach_tracing=False,
            app_config=config,
        )
        response = await model.ainvoke(prompt)
        draft = parse_draft_response(_response_text(response), fields=fields)
    except Exception:
        logger.warning(
            "DBTL setup drafting failed for project %s; returning a blank form",
            project_id,
            exc_info=True,
        )
        draft = empty_draft()

    return _serialize_draft(draft, enabled=True)


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
        outcome=str(ProposalOutcome(body.outcome)),
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
