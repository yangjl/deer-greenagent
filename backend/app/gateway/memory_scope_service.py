"""Resolve a project's memory scope migration from Gateway request state.

Keeps the router thin: everything that needs to know about DeerMem's storage
root, the authoritative membership set, or the migration journal lives here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, status

from deerflow.agents.memory.scopes.classify import ProjectAuthority
from deerflow.agents.memory.scopes.inventory import ScopeManifest, build_manifest
from deerflow.agents.memory.scopes.journal import MigrationJournal
from deerflow.agents.memory.scopes.migration import MigrationContext, MigrationPlan, plan_migration

logger = logging.getLogger(__name__)

JOURNAL_DIRNAME = ".scope-migration"


def resolve_scope_bindings(request: Request) -> tuple[Path, Any]:
    """Return ``(storage_root, fact_store)`` for the active memory backend.

    Test/embedding hosts may pin both on ``app.state``; otherwise the process
    singleton hands back its own store so no second instance competes for the
    same files.
    """
    root_override = getattr(request.app.state, "memory_storage_root", None)
    store_override = getattr(request.app.state, "memory_fact_store", None)
    if root_override is not None and store_override is not None:
        return Path(root_override), store_override

    from deerflow.agents.memory import get_memory_manager

    try:
        root, store = get_memory_manager().scope_bindings()
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="The configured memory backend does not support scope migration.",
        ) from exc
    return Path(root), store


def build_authorities(project: dict, members: list[dict]) -> tuple[ProjectAuthority, ...]:
    """Every ``(member, project)`` pair a legacy bucket could have come from."""
    project_id = str(project["id"])
    root_path = project.get("root_path") or None
    return tuple(
        ProjectAuthority(
            user_id=str(member["user_id"]),
            project_id=project_id,
            project_root=str(root_path) if root_path else None,
        )
        for member in members
    )


def build_project_manifest(
    storage_root: Path,
    project: dict,
    members: list[dict],
) -> ScopeManifest:
    """Inventory the memory root against this project's authoritative members."""
    return build_manifest(
        storage_root,
        build_authorities(project, members),
        known_user_ids=tuple(str(member["user_id"]) for member in members),
    )


def build_plan(
    storage_root: Path,
    project: dict,
    members: list[dict],
    *,
    pending_only: bool = True,
) -> MigrationPlan:
    project_id = str(project["id"])
    manifest = build_project_manifest(storage_root, project, members)
    return plan_project_manifest(
        storage_root,
        manifest,
        project_id,
        pending_only=pending_only,
    )


def plan_project_manifest(
    storage_root: Path,
    manifest: ScopeManifest,
    project_id: str,
    *,
    pending_only: bool = True,
) -> MigrationPlan:
    """Plan from one manifest snapshot so review metadata cannot drift."""
    completed = MigrationJournal(storage_root / JOURNAL_DIRNAME, project_id).completed_operations() if pending_only else ()
    return plan_migration(
        manifest,
        project_id,
        completed_operations=completed,
    )


def build_context(storage_root: Path, store: Any, project_id: str) -> MigrationContext:
    """A migration context whose journal lives beside — not inside — the buckets."""
    return MigrationContext(
        store=store,
        journal=MigrationJournal(storage_root / JOURNAL_DIRNAME, project_id),
        project_id=project_id,
        storage_root=storage_root,
    )
