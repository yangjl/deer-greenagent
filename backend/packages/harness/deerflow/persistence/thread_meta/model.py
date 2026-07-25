"""ORM model for thread metadata."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class ThreadMetaRow(Base):
    __tablename__ = "threads_meta"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    assistant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    scope_type: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="inbox",
        server_default="inbox",
    )
    visibility: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="private-owner",
        server_default="private-owner",
    )
    display_name: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(20), default="idle")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
