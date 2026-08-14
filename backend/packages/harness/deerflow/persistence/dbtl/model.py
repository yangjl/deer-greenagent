"""ORM models for the DBTL governance foundation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, event, false, text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DbtlStageTransitionImmutable(RuntimeError):
    """A durable path edge cannot be updated or deleted."""


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
    # Immutable provenance, intentionally not a foreign key: deleting a chat
    # must not erase which conversation launched the research record.
    originating_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
        # A project may run several top-level cycles at once (different traits,
        # populations, or seasons), so there is deliberately no uniqueness on
        # live cycles here — see migration 0018. The double-click guard that
        # index also provided lives on in the create-idempotency index below,
        # which is what actually collapses two simultaneous submissions.
        Index(
            "uq_dbtl_cycle_create_idempotency",
            "project_id",
            "create_idempotency_key",
            unique=True,
            sqlite_where=text("create_idempotency_key IS NOT NULL"),
            postgresql_where=text("create_idempotency_key IS NOT NULL"),
        ),
    )


class DbtlDiscoveryRow(Base):
    """One durable pre-cycle conversation bound to a project thread."""

    __tablename__ = "dbtl_discoveries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(96), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    draft_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    offered_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    package_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    package_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cycle_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("dbtl_cycles.id", ondelete="RESTRICT"), nullable=True)
    start_submission_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_dbtl_discovery_active_thread",
            "project_id",
            "thread_id",
            unique=True,
            sqlite_where=text("status IN ('gathering', 'ready', 'offered')"),
            postgresql_where=text("status IN ('gathering', 'ready', 'offered')"),
        ),
        UniqueConstraint("start_submission_id", name="uq_dbtl_discovery_start_submission"),
    )


class DbtlDiscoveryOutboxRow(Base):
    """One replayable, human-visible effect of a discovery transition.

    The outbox identity is also the message/event identity used by the graph.
    A retry can therefore publish the same effect without creating a second
    card, receipt, package projection, or Design kickoff.
    """

    __tablename__ = "dbtl_discovery_outbox"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    discovery_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dbtl_discoveries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_dbtl_discovery_outbox_pending",
            "status",
            "created_at",
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


class DbtlStageTransitionRow(Base):
    """One human-decided boundary on the cycle's stage-graph walk.

    Append-only: the path history *is* this table ordered by ``seq``. A row
    records who decided, what the agent assessed and recommended (later
    phases), which routes were offered, and the evidence/dataset/spec/policy
    bindings the decision was made against — so refreshing a browser or
    restoring a checkpoint reconstructs the same path from durable records.
    Reconciliation is not a stage: no transition row is ever written for data
    work (progressive-gate plan §4).
    """

    __tablename__ = "dbtl_stage_transitions"

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
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    from_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    from_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage_attempt_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    chosen_route: Mapped[str] = mapped_column(String(64), nullable=False)
    to_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    # Phase 1 fields, nullable until the assessment exists: the agent's view
    # and the human's override are both kept, so the record shows who decided.
    assessed_difficulty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    assessment_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_override: Mapped[str | None] = mapped_column(String(16), nullable=True)
    offered_routes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_surface_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dataset_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage_spec_version: Mapped[str | None] = mapped_column(String(96), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(96), nullable=True)
    backfilled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint("cycle_id", "seq", name="uq_dbtl_stage_transition_seq"),
        Index("ix_dbtl_stage_transitions_cycle_seq", "cycle_id", "seq"),
    )


@event.listens_for(DbtlStageTransitionRow, "before_update")
def _refuse_stage_transition_update(*_args: object) -> None:
    raise DbtlStageTransitionImmutable("DBTL stage transitions are append-only and cannot be updated.")


@event.listens_for(DbtlStageTransitionRow, "before_delete")
def _refuse_stage_transition_delete(*_args: object) -> None:
    raise DbtlStageTransitionImmutable("DBTL stage transitions are append-only and cannot be deleted.")


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


class DbtlStageStepRunRow(Base):
    """One immutable attempt at one Build workflow step.

    Deliberately **not** an overload of `dbtl_stage_worker_runs`. Worker rows
    describe model-backed work units; `load_design` and `render_review_deck` are
    deterministic server code that needs the same audit and retry semantics
    without pretending to be an agent — a deterministic step recorded as a
    "worker" would make the review record claim a model read the Design.

    Append-only. A retry appends an attempt; it never overwrites the failed
    record, because a failed attempt is what a reviewer reads to understand a
    retry. Only one attempt per step may be `running`, enforced by a partial
    unique index rather than by a check-then-write.

    Phase identity (`phase_index`, `phase_key`, `plan_digest`) is nullable
    because only `execute_phases` expands; a phase attempt whose plan changed is
    a *different* phase rather than a retry of this one, which is why the plan
    digest is on the row and not merely on its container.
    """

    __tablename__ = "dbtl_stage_step_runs"

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
    workflow_spec_key: Mapped[str] = mapped_column(String(96), nullable=False)
    step_key: Mapped[str] = mapped_column(String(48), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)

    #: Phase identity. Null for the four non-container steps.
    phase_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phase_key: Mapped[str | None] = mapped_column(String(96), nullable=True)
    #: `phase_key` normalized to a non-null value, and the column both
    #: uniqueness rules are actually written against.
    #:
    #: SQL treats NULLs as distinct, so indexing the nullable `phase_key`
    #: constrained nothing for the four non-phase steps — which is every step
    #: but one. Two identical `running` rows and two attempt-1 rows were both
    #: accepted. `phase_key` stays nullable because "this step has no phase" is
    #: the honest answer to a reader; the sentinel exists only so the database
    #: can compare it.
    phase_slot: Mapped[str] = mapped_column(String(96), nullable=False, default="", server_default="")
    plan_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: What was asked for and who covered it. `via_generalist` is what lets a
    #: reviewer tell a real specialist from a stand-in.
    capability: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_name: Mapped[str | None] = mapped_column(String(96), nullable=True)
    via_generalist: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())

    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    output_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The exact predecessor attempts this one was computed against.
    predecessor_step_run_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    parent_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(160), nullable=True)

    #: Bounded and server-owned. Never raw prompts, secrets, unbounded shell
    #: output, or model reasoning.
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_summary: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    execution: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    #: A paused step's collaboration request, or a bound Build work meeting.
    human_input_request_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    meeting_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    #: Which attempt this one replaces, for reading a retry chain.
    supersedes_step_run_id: Mapped[str | None] = mapped_column(String(96), nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "stage_attempt_id",
            "step_key",
            "phase_slot",
            "attempt",
            name="uq_dbtl_stage_step_attempt",
        ),
        Index(
            "ix_dbtl_stage_step_runs_lookup",
            "stage_attempt_id",
            "step_key",
        ),
        # "Only one attempt for a step may be `running`", enforced where two
        # concurrent dispatches cannot both win. Mirrored in migration 0026 so
        # the `create_all` bootstrap path and the migrated path agree. Keyed on
        # `phase_slot`, never `phase_key`: a NULL compares unequal to itself, so
        # the nullable column made this index a no-op for every ordinary step.
        Index(
            "uq_dbtl_stage_step_running",
            "stage_attempt_id",
            "step_key",
            "phase_slot",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
    )


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
    # Empty means the row predates the typed rerun contract. Keeping that
    # distinction explicit lets Test read old lineage without treating prose as
    # an executable, server-verified rerun record.
    rerun_spec: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
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
    build_lineage_id: Mapped[str | None] = mapped_column(
        String(96),
        ForeignKey("dbtl_build_lineage.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    evidence_exception_artifact_id: Mapped[str | None] = mapped_column(
        String(96),
        ForeignKey("dbtl_artifacts.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    evidence_exception_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
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
    input_source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="design_sheet",
        server_default="design_sheet",
    )
    feedback_surface_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    deck_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deck_schema_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    selected_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    selected_card_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    human_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale_projection: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_dbtl_review_idempotency"),
        Index("ix_dbtl_reviews_cycle_created", "cycle_id", "created_at"),
    )


class DbtlDesignFeedbackSurfaceRow(Base):
    """One rendered Design deck, bound to the work it projects.

    This is deliberately **not** a review and does not replace
    ``dbtl_reviews``. It answers a narrower question the review path cannot:
    given some HTML that claims to be a Design deck, did this workflow produce
    it, from which evidence, and which conversation may it answer? Without that
    binding, any page an agent wrote could impersonate the deck.

    The evidence columns are nullable because a paused meeting has no package
    yet — the chair stopped to ask something before it could write one. A
    ``stage_review`` surface without them is refused at the write boundary,
    since a verdict must bind to a document.
    """

    __tablename__ = "dbtl_design_feedback_surfaces"

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
    #: Additive Phase 2 discriminator. The legacy table name remains for one
    #: compatibility window so captured Design decks and old foreign keys keep
    #: resolving byte-for-byte.
    # The server defaults mirror migration 0025, which needs them to backfill
    # rows written before the stage dimension existed. A create_all schema
    # without them is a different schema, and the bootstrap comparison says so.
    stage: Mapped[str] = mapped_column(String(24), nullable=False, default="design", server_default="design")
    surface_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    design_round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: The only conversation this surface may answer. Work continues where it
    #: started; a deck opened from another view may display, never mutate.
    originating_thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    chair_worker_run_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    human_input_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Server-recorded decision request used to validate an option without
    #: trusting the iframe to repeat the chair's choices honestly.
    decision_request: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    deck_uri: Mapped[str] = mapped_column(Text, nullable=False)
    #: The exact bytes shown. A review binds this beside the evidence hash, so
    #: an audit can answer both "what was authoritative" and "what was read".
    deck_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    deck_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_artifact_id: Mapped[str | None] = mapped_column(
        String(96),
        ForeignKey("dbtl_artifacts.id", ondelete="CASCADE"),
        nullable=True,
    )
    evidence_artifact_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bound_db_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(96), nullable=False)
    #: Regeneration supersedes rather than mutates: the old row stays readable
    #: because it is the record of what somebody was actually shown.
    superseded_by_surface_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "stage_attempt_id",
            "mode",
            "deck_content_hash",
            name="uq_dbtl_design_feedback_deck",
        ),
        Index("ix_dbtl_design_feedback_cycle_created", "cycle_id", "created_at"),
    )


class DbtlDesignFeedbackActionRow(Base):
    """One single-use, payload-bound intent accepted from a Design deck."""

    __tablename__ = "dbtl_design_feedback_actions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
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
    surface_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("dbtl_design_feedback_surfaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(24), nullable=False, default="design", server_default="design")
    action_group: Mapped[str] = mapped_column(String(32), nullable=False)
    action_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_card_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    human_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    slide_comments: Mapped[dict[str, str]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    active_slide_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_db_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    expected_deck_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    run_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    receipt: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_dbtl_design_feedback_action_id"),
        UniqueConstraint("surface_id", "action_group", name="uq_dbtl_design_feedback_action_group"),
        Index("ix_dbtl_design_feedback_action_cycle_created", "cycle_id", "created_at"),
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


class KnowledgePublicationRow(Base):
    """One explicit claim publication to one selected target project."""

    __tablename__ = "knowledge_publications"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("knowledge_claims.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    target_project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    pointer: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    published_by: Mapped[str] = mapped_column(String(64), nullable=False)
    publisher_project_role: Mapped[str] = mapped_column(String(24), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("claim_id", "target_project_id", name="uq_knowledge_publication_target"),)


class KnowledgeEventRow(Base):
    """Immutable audit history for candidates, claims, and publications."""

    __tablename__ = "knowledge_events"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    cycle_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("dbtl_cycles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    candidate_id: Mapped[str | None] = mapped_column(
        String(96),
        ForeignKey("memory_candidates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    claim_id: Mapped[str | None] = mapped_column(
        String(96),
        ForeignKey("knowledge_claims.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    publication_id: Mapped[str | None] = mapped_column(
        String(96),
        ForeignKey("knowledge_publications.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_knowledge_event_idempotency",
        ),
    )


class DbtlBuildCollaborationRow(Base):
    """One pause in a Build, and the human answer that released it.

    A paused step already records *that* it is waiting (`needs_input`) and the
    sentence it is waiting on. What it cannot record is the exchange: which
    formats were offered, which option the person chose, in whose name, against
    which cycle revision, and which attempt resumed as a result. Without that a
    resumed Build cannot be reconstructed — and "who decided to replan" is
    exactly the question a reviewer asks when the phases beneath a plan are
    gone.

    Two rules live in the schema rather than in calling code.

    **One control may be open per stage attempt.** A Build pauses at exactly one
    place, so a second open request means a stale card is competing with a live
    one for the same answer. The partial unique index is the arbiter, because
    two concurrent dispatches can both pass a check-then-write.

    **A response is idempotent by request plus client submission id, and a
    changed answer under the same id is a conflict rather than a silent
    overwrite.** A double-clicked button and a genuinely different decision look
    identical at the HTTP boundary; only the payload tells them apart.
    """

    __tablename__ = "dbtl_build_collaborations"

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
    workflow_spec_key: Mapped[str] = mapped_column(String(96), nullable=False)

    #: Which pause this is, and what it is bound to. The digests are what make a
    #: replayed answer detectable: an answer to a plan that has since been
    #: redrawn names a `plan_digest` nothing selects any more.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    step_key: Mapped[str] = mapped_column(String(48), nullable=False)
    step_run_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    plan_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bound_cycle_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    #: The card as it was shown. Retained because a review of a resumed Build
    #: has to be able to see the choice as it was offered, not as it would be
    #: rendered today.
    request_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    response_format: Mapped[str] = mapped_column(String(24), nullable=False, default="single_choice", server_default="single_choice")
    question: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    options: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    recommended_option_id: Mapped[str | None] = mapped_column(String(48), nullable=True)

    #: `open`, `answered`, `held`, `superseded`, `stale`, or `cancelled`.
    lifecycle: Mapped[str] = mapped_column(String(24), nullable=False, default="open", server_default="open", index=True)

    #: The answer, verbatim. Never a paraphrase, and never attributed to an
    #: agent: `responder_user_id` is taken from the authenticated run, not from
    #: the reply.
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    response_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    responder_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    client_submission_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Where the exchange happened, and what it started.
    originating_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resumed_step_run_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    meeting_id: Mapped[str | None] = mapped_column(String(96), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "request_id",
            "client_submission_id",
            name="uq_dbtl_build_collab_submission",
        ),
        Index(
            "ix_dbtl_build_collab_attempt",
            "stage_attempt_id",
            "lifecycle",
        ),
        # "Only one collaboration request may be open for one step attempt."
        Index(
            "uq_dbtl_build_collab_open",
            "stage_attempt_id",
            unique=True,
            sqlite_where=text("lifecycle = 'open'"),
            postgresql_where=text("lifecycle = 'open'"),
        ),
    )
