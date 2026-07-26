"""Project-scoped memory migration API (Phase 2).

Privacy shapes every route here:

* the landing view returns **counts**, never a fact body;
* the review queue returns bodies for exactly one person — their owner;
* a decision names **one** fact, because bulk "share all" is deliberately
  absent from the product;
* the source bucket is resolved from the authenticated caller, never from the
  request body, so a member cannot share a colleague's private memory.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.gateway.authz import require_permission
from app.gateway.deps import get_workspace_repo
from app.gateway.memory_scope_service import (
    build_context,
    build_plan,
    build_project_manifest,
    plan_project_manifest,
    resolve_scope_bindings,
)
from deerflow.agents.memory.scopes.classify import BucketClassification
from deerflow.agents.memory.scopes.inventory import read_fact_document
from deerflow.agents.memory.scopes.migration import (
    Decision,
    MigrationOutcome,
    apply_decision,
    rollback_migration,
)

router = APIRouter(prefix="/api", tags=["memory-scope"])


class ReviewedSourceChangedError(RuntimeError):
    """The source no longer matches the bytes shown to the reviewer."""


class DecisionRequest(BaseModel):
    """One reviewer decision about one fact."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=1, max_length=128)
    agent_name: str = Field(min_length=1, max_length=128)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["keep_private", "share", "edit_then_share", "quarantine"]
    edited_content: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def edit_requires_content(self) -> DecisionRequest:
        if self.decision == "edit_then_share" and not (self.edited_content or "").strip():
            raise ValueError("edit_then_share requires non-empty edited_content")
        return self


async def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return str(user.id)


async def _project_and_members(project_id: str, request: Request) -> tuple[dict, list[dict], str]:
    """Resolve the project for a member, or 404. Membership is the gate."""
    user_id = await _user_id(request)
    repo = get_workspace_repo(request)
    project = await repo.get_project(project_id, user_id=user_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    members = await repo.list_project_members(project_id, user_id=user_id)
    return project, members, user_id


@router.get("/projects/{project_id}/memory/migration")
@require_permission("threads", "read")
async def get_migration_overview(project_id: str, request: Request):
    """Counts only. No fact body is served by this route, ever."""
    project, members, user_id = await _project_and_members(project_id, request)
    storage_root, _store = resolve_scope_bindings(request)
    plan = await asyncio.to_thread(build_plan, storage_root, project, members)
    return {"project_id": project_id, "counts": plan.for_user(user_id).counts.to_dict()}


@router.get("/projects/{project_id}/memory/migration/suggestions")
@require_permission("threads", "read")
async def get_migration_suggestions(project_id: str, request: Request):
    """The caller's own review queue — one fact at a time, with its content."""
    project, members, user_id = await _project_and_members(project_id, request)
    storage_root, _store = resolve_scope_bindings(request)

    def _collect() -> list[dict]:
        manifest = build_project_manifest(storage_root, project, members)
        plan = plan_project_manifest(
            storage_root,
            manifest,
            project_id,
        ).for_user(user_id)
        by_key = {(entry.bucket_id, entry.agent_name, entry.fact_id): entry for entry in manifest.facts}
        rows: list[dict] = []
        for item in plan.suggestions_for(user_id):
            entry = by_key.get((item.bucket_id, item.agent_name, item.fact_id))
            document = read_fact_document(storage_root, entry.relative_path) if entry else {"title": "", "content": ""}
            rows.append({**item.to_dict(), "title": document["title"], "content": document["content"]})
        return rows

    return {"project_id": project_id, "suggestions": await asyncio.to_thread(_collect)}


@router.post("/projects/{project_id}/memory/migration/decisions")
@require_permission("threads", "write")
async def submit_migration_decision(project_id: str, body: DecisionRequest, request: Request):
    """Apply one decision to one of the caller's own facts."""
    project, members, user_id = await _project_and_members(project_id, request)
    storage_root, store = resolve_scope_bindings(request)

    def _apply() -> tuple[str, str]:
        manifest = build_project_manifest(storage_root, project, members)
        plan = plan_project_manifest(
            storage_root,
            manifest,
            project_id,
            pending_only=False,
        )
        candidates = [item for item in plan.suggestions_for(user_id) if item.fact_id == body.fact_id and item.agent_name == body.agent_name]
        if not candidates:
            # Also the answer when the fact belongs to another member: this
            # route never confirms the existence of someone else's memory.
            raise LookupError(body.fact_id)
        matching = [item for item in candidates if item.sha256 == body.source_sha256]
        if len(matching) != 1:
            raise ReviewedSourceChangedError
        suggestion = matching[0]
        entry = next(fact for fact in manifest.facts if fact.fact_id == suggestion.fact_id and fact.bucket_id == suggestion.bucket_id and fact.agent_name == suggestion.agent_name and fact.sha256 == suggestion.sha256)
        result = apply_decision(
            entry,
            Decision(body.decision),
            build_context(storage_root, store, project_id),
            edited_content=body.edited_content,
        )
        return result.outcome.value, result.detail

    try:
        outcome, detail = await asyncio.to_thread(_apply)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory fact not found") from exc
    except ReviewedSourceChangedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This fact changed since it was reviewed; reload the queue and decide again.",
        ) from exc

    if outcome in {
        MigrationOutcome.SOURCE_CHANGED.value,
        MigrationOutcome.SOURCE_MISSING.value,
        MigrationOutcome.DECISION_CONFLICT.value,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail or "This fact changed since it was reviewed; reload the queue and decide again.",
        )
    return {"fact_id": body.fact_id, "decision": body.decision, "outcome": outcome}


@router.get("/projects/{project_id}/memory/migration/manifest")
@require_permission("threads", "read")
async def download_migration_manifest(project_id: str, request: Request):
    """The checksum-preserving evidence artifact. Carries no fact bodies."""
    project, members, user_id = await _project_and_members(project_id, request)
    storage_root, _store = resolve_scope_bindings(request)
    manifest = await asyncio.to_thread(build_project_manifest, storage_root, project, members)
    return manifest.for_project_member(project_id, user_id).to_dict()


@router.post("/projects/{project_id}/memory/migration/rollback")
@require_permission("threads", "write")
async def rollback_project_migration(project_id: str, request: Request):
    """Remove shared copies sourced from the caller's private project memory."""
    project, members, user_id = await _project_and_members(project_id, request)
    storage_root, store = resolve_scope_bindings(request)
    manifest = await asyncio.to_thread(build_project_manifest, storage_root, project, members)
    source_bucket_ids = {bucket.bucket_id for bucket in manifest.buckets if bucket.classification is BucketClassification.PROJECT and bucket.project_id == project_id and bucket.user_id == user_id}
    reverted = await asyncio.to_thread(
        rollback_migration,
        build_context(storage_root, store, project_id),
        source_bucket_ids=source_bucket_ids,
    )
    return {"project_id": project_id, "reverted": reverted}
