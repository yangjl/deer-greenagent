"""SQL repository for workspace membership and breeding projects."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.workspaces.model import ProjectRow, WorkspaceMemberRow, WorkspaceRow
from deerflow.utils.time import coerce_iso


class WorkspaceAccessDenied(LookupError):
    """The caller is not an active member of the requested workspace."""


class WorkspaceSlugConflict(ValueError):
    """A workspace already uses the requested slug."""


class ProjectSlugConflict(ValueError):
    """A project in this workspace already uses the requested slug."""


class WorkspaceRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def _serialize(row: WorkspaceRow | ProjectRow) -> dict[str, Any]:
        data = row.to_dict()
        for key in ("created_at", "updated_at"):
            value = data.get(key)
            if isinstance(value, datetime):
                data[key] = coerce_iso(value)
        return data

    async def create_workspace(
        self,
        *,
        workspace_id: str,
        name: str,
        slug: str,
        description: str | None,
        created_by: str,
    ) -> dict[str, Any]:
        workspace = WorkspaceRow(
            id=workspace_id,
            name=name,
            slug=slug,
            description=description,
            created_by=created_by,
        )
        membership = WorkspaceMemberRow(
            workspace_id=workspace_id,
            user_id=created_by,
            role="owner",
            status="active",
        )
        async with self._sf() as session:
            try:
                session.add(workspace)
                # Flush the parent first because these lightweight models do
                # not define ORM relationships that would otherwise give the
                # unit-of-work an insertion dependency.
                await session.flush()
                session.add(membership)
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise WorkspaceSlugConflict(slug) from exc
            await session.refresh(workspace)
            data = self._serialize(workspace)
            data["current_user_role"] = "owner"
            data["project_count"] = 0
            return data

    async def list_workspaces(self, user_id: str) -> list[dict[str, Any]]:
        project_counts = select(ProjectRow.workspace_id, func.count(ProjectRow.id).label("project_count")).group_by(ProjectRow.workspace_id).subquery()
        stmt = (
            select(
                WorkspaceRow,
                WorkspaceMemberRow.role,
                func.coalesce(project_counts.c.project_count, 0),
            )
            .join(
                WorkspaceMemberRow,
                WorkspaceMemberRow.workspace_id == WorkspaceRow.id,
            )
            .outerjoin(project_counts, project_counts.c.workspace_id == WorkspaceRow.id)
            .where(
                WorkspaceMemberRow.user_id == user_id,
                WorkspaceMemberRow.status == "active",
                WorkspaceRow.status == "active",
            )
            .order_by(WorkspaceRow.updated_at.desc(), WorkspaceRow.id.asc())
        )
        async with self._sf() as session:
            rows = await session.execute(stmt)
            result: list[dict[str, Any]] = []
            for workspace, role, project_count in rows:
                data = self._serialize(workspace)
                data["current_user_role"] = role
                data["project_count"] = int(project_count)
                result.append(data)
            return result

    async def _membership_role(
        self,
        session: AsyncSession,
        *,
        workspace_id: str,
        user_id: str,
    ) -> str | None:
        membership = await session.get(
            WorkspaceMemberRow,
            {"workspace_id": workspace_id, "user_id": user_id},
        )
        if membership is None or membership.status != "active":
            return None
        return membership.role

    async def list_projects(self, workspace_id: str, *, user_id: str) -> list[dict[str, Any]]:
        async with self._sf() as session:
            if await self._membership_role(session, workspace_id=workspace_id, user_id=user_id) is None:
                raise WorkspaceAccessDenied(workspace_id)
            stmt = (
                select(ProjectRow)
                .where(
                    ProjectRow.workspace_id == workspace_id,
                    ProjectRow.status == "active",
                )
                .order_by(ProjectRow.updated_at.desc(), ProjectRow.id.asc())
            )
            rows = await session.execute(stmt)
            return [self._serialize(row) for row in rows.scalars()]

    async def get_project(self, project_id: str, *, user_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            project = await session.get(ProjectRow, project_id)
            if project is None:
                return None
            if (
                await self._membership_role(
                    session,
                    workspace_id=project.workspace_id,
                    user_id=user_id,
                )
                is None
            ):
                return None
            return self._serialize(project)

    async def create_project(
        self,
        *,
        project_id: str,
        workspace_id: str,
        name: str,
        slug: str,
        description: str | None,
        crop_profile: str,
        created_by: str,
        root_path: str | None = None,
    ) -> dict[str, Any]:
        async with self._sf() as session:
            role = await self._membership_role(
                session,
                workspace_id=workspace_id,
                user_id=created_by,
            )
            if role not in {"owner", "admin", "member"}:
                raise WorkspaceAccessDenied(workspace_id)
            project = ProjectRow(
                id=project_id,
                workspace_id=workspace_id,
                name=name,
                slug=slug,
                description=description,
                crop_profile=crop_profile,
                created_by=created_by,
                root_path=root_path,
            )
            session.add(project)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ProjectSlugConflict(slug) from exc
            await session.refresh(project)
            return self._serialize(project)

    async def get_project_record(self, project_id: str) -> dict[str, Any] | None:
        """Unscoped internal read (no membership check).

        For server-side scope resolution where access was already established
        through the conversation's owner — never expose through a route.
        """
        async with self._sf() as session:
            row = await session.get(ProjectRow, project_id)
            return self._serialize(row) if row is not None else None

    async def update_project_root(self, project_id: str, root_path: str) -> None:
        """Backfill/repair the project's human-visible folder path."""
        async with self._sf() as session:
            row = await session.get(ProjectRow, project_id)
            if row is None:
                return
            row.root_path = root_path
            await session.commit()
