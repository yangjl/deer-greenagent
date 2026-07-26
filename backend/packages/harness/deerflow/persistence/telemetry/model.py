"""ORM model for classifier shadow-evaluation telemetry (Phase 4).

This table lives outside ``deerflow.persistence.dbtl`` on purpose. Telemetry
about what the classifier *thought* is not a research record, and keeping it
in a separate module with no DBTL import is what makes "a shadow event cannot
mutate cycle state" a property of the code rather than a promise about it.

The row deliberately stores rule hits and the derived objective but **not** the
user's message. Chat content in a telemetry table would outlive the
conversation and sit outside the memory-scope controls Phase 2 built for
exactly that data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ClassifierEvaluationRow(Base):
    """One shadow evaluation, plus the human choice that followed it."""

    __tablename__ = "dbtl_classifier_evaluations"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # What routing concluded, and which rung of the precedence ladder answered.
    route_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    route_source: Mapped[str] = mapped_column(String(32), nullable=False)
    # SHA-256 over the request text plus routing context. It binds an
    # idempotency key to one semantic request without storing another copy of
    # the conversation in telemetry.
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    band: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    rule_hits_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    missing_fields_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    proposed_objective: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    policy_version: Mapped[str] = mapped_column(String(96), nullable=False)

    # Null until the human reacts. That gap is the shadow-mode signal.
    human_choice: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        Index("ix_dbtl_classifier_eval_project_created", "project_id", "created_at"),
        Index("ix_dbtl_classifier_eval_project_choice", "project_id", "human_choice"),
    )
