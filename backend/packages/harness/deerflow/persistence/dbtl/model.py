"""ORM models for the DBTL governance foundation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DbtlCycleRow(Base):
    __tablename__ = "dbtl_cycles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # A computational cycle may hang off a season/program parent. Self-FK with
    # NO ACTION rather than CASCADE: deleting a parent must not silently erase
    # the child research records underneath it.
    parent_cycle_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "dbtl_cycles.id",
            name="fk_dbtl_cycles_parent_cycle_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    create_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    cycle_class: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(48), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(96), nullable=False)
    db_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    projection_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
    )

    __table_args__ = (
        Index("ix_dbtl_cycles_project_state", "project_id", "state"),
        # At most one live top-level cycle per project. Enforced by the
        # database, not by a read-then-write check, because two concurrent
        # "Start a cycle" clicks would both pass an application-level check.
        # Child cycles (parent_cycle_id NOT NULL) are exempt: a season/program
        # parent may carry several computational children at once.
        Index(
            "uq_dbtl_active_top_level_cycle",
            "project_id",
            unique=True,
            sqlite_where=text("parent_cycle_id IS NULL AND state NOT IN ('completed', 'abandoned')"),
            postgresql_where=text("parent_cycle_id IS NULL AND state NOT IN ('completed', 'abandoned')"),
        ),
        Index(
            "uq_dbtl_cycle_create_idempotency",
            "project_id",
            "create_idempotency_key",
            unique=True,
            sqlite_where=text("create_idempotency_key IS NOT NULL"),
            postgresql_where=text("create_idempotency_key IS NOT NULL"),
        ),
    )


class DbtlStageAttemptRow(Base):
    __tablename__ = "dbtl_stage_runs"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cycle_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dbtl_cycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    db_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    # Which versioned StageSpec this attempt ran under (Phase 6). Nullable
    # because Phase 3 cycles predate the registry; an attempt that cannot name
    # its contract simply has no approval to invalidate.
    stage_spec_key: Mapped[str | None] = mapped_column(String(96), nullable=True)
    # The dataset fingerprint an approval was bound to. Set when a stage is
    # approved, compared afterwards: this is what turns "a dataset changed" from
    # an assumption into a detection.
    approved_dataset_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_policy_version: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
    )

    __table_args__ = (
        UniqueConstraint("cycle_id", "stage", "attempt_number", name="uq_dbtl_stage_attempt"),
        Index("ix_dbtl_stage_project_cycle", "project_id", "cycle_id"),
    )


class DbtlDatasetRow(Base):
    """One declared input to a cycle, pinned by content hash (Phase 6).

    The hash is what makes "if a dataset changes, the reconciliation gate is
    invalidated" enforceable: an approval binds the fingerprint of this whole
    set, and a later comparison detects the change instead of assuming none.

    ``declared_immutable`` is the steward's assertion that Build must not write
    here. It is stored rather than inferred from a filesystem mode because the
    assertion is the thing a reviewer approves, and a permission bit can change
    without anyone having decided anything.
    """

    __tablename__ = "dbtl_datasets"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cycle_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dbtl_cycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_key: Mapped[str] = mapped_column(String(120), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    role: Mapped[str] = mapped_column(String(24), nullable=False, default="raw")
    recorded_by: Mapped[str] = mapped_column(String(64), nullable=False)
    db_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
    )

    __table_args__ = (
        # One declaration per source per cycle. A second row for the same key
        # would make the fingerprint depend on which one a query happened to
        # read, so redeclaring updates in place and is recorded as an event.
        UniqueConstraint("cycle_id", "source_key", name="uq_dbtl_dataset_source"),
    )


class DbtlStageWorkerRunRow(Base):
    """One worker's contribution to one stage attempt (Phase 6).

    Persisted even when the worker failed. A fan-out where two of three workers
    crashed must not read as a tidy run with one worker, and the reviewer needs
    to tell a crash apart from a worker that ran and found nothing.
    """

    __tablename__ = "dbtl_stage_worker_runs"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cycle_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dbtl_cycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage_attempt_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("dbtl_stage_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    unit_id: Mapped[str] = mapped_column(String(160), nullable=False)
    stage_spec_key: Mapped[str] = mapped_column(String(96), nullable=False)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(96), nullable=False)
    via_generalist: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    stop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (UniqueConstraint("stage_attempt_id", "unit_id", name="uq_dbtl_stage_worker_unit"),)


class DbtlBuildLineageRow(Base):
    """One revision of the reproducibility record for a Build attempt."""

    __tablename__ = "dbtl_build_lineage"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cycle_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dbtl_cycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage_attempt_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("dbtl_stage_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lineage_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_spec_key: Mapped[str] = mapped_column(String(96), nullable=False)
    dataset_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    code_revision: Mapped[str] = mapped_column(String(160), nullable=False)
    config_revision: Mapped[str] = mapped_column(String(160), nullable=False)
    environment: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_artifacts: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    output_artifacts: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    deviations: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    logs_uri: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_by: Mapped[str] = mapped_column(String(64), nullable=False)
    db_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
    )

    __table_args__ = (
        UniqueConstraint(
            "stage_attempt_id",
            "lineage_revision",
            name="uq_dbtl_build_lineage_revision",
        ),
    )


class DbtlValidityAssessmentRow(Base):
    """A human-owned Test validity decision, separate from headline metrics."""

    __tablename__ = "dbtl_validity_assessments"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cycle_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dbtl_cycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    test_stage_attempt_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("dbtl_stage_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    build_lineage_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("dbtl_build_lineage.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    assessment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    validity_pack_key: Mapped[str] = mapped_column(String(96), nullable=False)
    headline_metrics: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    checks: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    recommendation: Mapped[str] = mapped_column(String(48), nullable=False)
    reason_codes: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    limitations: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reviewer_project_role: Mapped[str] = mapped_column(String(24), nullable=False)
    db_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
    )

    __table_args__ = (
        UniqueConstraint(
            "test_stage_attempt_id",
            "assessment_revision",
            name="uq_dbtl_validity_assessment_revision",
        ),
    )


class DbtlArtifactRow(Base):
    __tablename__ = "dbtl_artifacts"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cycle_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dbtl_cycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage_attempt_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("dbtl_stage_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "stage_attempt_id",
            "artifact_type",
            "revision",
            name="uq_dbtl_artifact_revision",
        ),
    )


class DbtlReviewRow(Base):
    __tablename__ = "dbtl_reviews"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cycle_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dbtl_cycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage_attempt_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("dbtl_stage_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("dbtl_artifacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    bound_db_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    bound_stage_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    bound_projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(96), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reviewer_project_role: Mapped[str] = mapped_column(String(24), nullable=False)
    authorization_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_dbtl_review_idempotency"),
        Index("ix_dbtl_reviews_cycle_created", "cycle_id", "created_at"),
    )


class DbtlEventRow(Base):
    __tablename__ = "activity_events"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cycle_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dbtl_cycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (UniqueConstraint("cycle_id", "sequence", name="uq_dbtl_event_sequence"),)


class DbtlValidationRow(Base):
    __tablename__ = "dbtl_validations"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    database_backend: Mapped[str] = mapped_column(String(24), nullable=False)
    technical_ready: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)


class DbtlCutoverDecisionRow(Base):
    __tablename__ = "dbtl_cutover_decisions"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    validation_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("dbtl_validations.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)


class DbtlTransitionIntentRow(Base):
    __tablename__ = "dbtl_transition_intents"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    cycle_id: Mapped[str] = mapped_column(String(64), ForeignKey("dbtl_cycles.id", ondelete="CASCADE"), nullable=False, index=True)
    expected_db_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[str] = mapped_column(String(48), nullable=False)
    proposed_state: Mapped[str] = mapped_column(String(48), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (UniqueConstraint("project_id", "idempotency_key", name="uq_dbtl_transition_intent_idempotency"),)


class DbtlTransitionRow(Base):
    __tablename__ = "dbtl_transitions"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    cycle_id: Mapped[str] = mapped_column(String(64), ForeignKey("dbtl_cycles.id", ondelete="CASCADE"), nullable=False, index=True)
    transition_intent_id: Mapped[str] = mapped_column(String(96), ForeignKey("dbtl_transition_intents.id", ondelete="CASCADE"), nullable=False, unique=True)
    committed_db_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[str] = mapped_column(String(48), nullable=False)
    to_state: Mapped[str] = mapped_column(String(48), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)


class DbtlGateEvaluationRow(Base):
    __tablename__ = "dbtl_gate_evaluations"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    cycle_id: Mapped[str] = mapped_column(String(64), ForeignKey("dbtl_cycles.id", ondelete="CASCADE"), nullable=False, index=True)
    transition_intent_id: Mapped[str] = mapped_column(String(96), ForeignKey("dbtl_transition_intents.id", ondelete="CASCADE"), nullable=False, index=True)
    expected_db_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_hashes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_tool_name: Mapped[str] = mapped_column(String(96), nullable=False)
    policy_tool_version: Mapped[str] = mapped_column(String(96), nullable=False)
    policy_input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_output: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)


class WorkItemRow(Base):
    __tablename__ = "work_items"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    cycle_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("dbtl_cycles.id", ondelete="CASCADE"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    db_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)


class MemoryCandidateRow(Base):
    __tablename__ = "memory_candidates"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    cycle_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("dbtl_cycles.id", ondelete="SET NULL"), nullable=True, index=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)


class KnowledgeClaimRow(Base):
    __tablename__ = "knowledge_claims"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)


class KnowledgePromotionRow(Base):
    __tablename__ = "knowledge_promotions"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    memory_candidate_id: Mapped[str] = mapped_column(String(96), ForeignKey("memory_candidates.id", ondelete="RESTRICT"), nullable=False, index=True)
    knowledge_claim_id: Mapped[str] = mapped_column(String(96), ForeignKey("knowledge_claims.id", ondelete="RESTRICT"), nullable=False, index=True)
    promoted_by: Mapped[str] = mapped_column(String(64), nullable=False)
    authorization_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)


class KnowledgeLinkRow(Base):
    __tablename__ = "knowledge_links"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    from_claim_id: Mapped[str] = mapped_column(String(96), ForeignKey("knowledge_claims.id", ondelete="CASCADE"), nullable=False)
    to_claim_id: Mapped[str] = mapped_column(String(96), ForeignKey("knowledge_claims.id", ondelete="CASCADE"), nullable=False)
    relationship: Mapped[str] = mapped_column(String(48), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (UniqueConstraint("from_claim_id", "to_claim_id", "relationship", name="uq_knowledge_link"),)
