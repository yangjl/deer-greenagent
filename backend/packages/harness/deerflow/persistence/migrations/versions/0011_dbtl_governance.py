"""Add the durable DBTL governance foundation.

Revision ID: 0011_dbtl_governance
Revises: 0010_project_root_path
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_dbtl_governance"
down_revision = "0010_project_root_path"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A pre-Alembic database may have been opened once by this application
    # build, whose create_all path materializes the complete DBTL foundation.
    # Treat that complete shape as already provisioned before stamping head.
    expected_tables = {
        "dbtl_cycles",
        "dbtl_stage_runs",
        "dbtl_artifacts",
        "dbtl_reviews",
        "activity_events",
        "dbtl_transition_intents",
        "dbtl_transitions",
        "dbtl_gate_evaluations",
        "work_items",
        "memory_candidates",
        "knowledge_claims",
        "knowledge_promotions",
        "knowledge_links",
        "dbtl_validations",
        "dbtl_cutover_decisions",
    }
    if expected_tables <= set(sa.inspect(op.get_bind()).get_table_names()):
        return

    op.create_table(
        "dbtl_cycles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("cycle_class", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=48), nullable=False),
        sa.Column("policy_version", sa.String(length=96), nullable=False),
        sa.Column("db_revision", sa.Integer(), nullable=False),
        sa.Column("projection_json", sa.JSON(), nullable=False),
        sa.Column("projection_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dbtl_cycles_project_id", "dbtl_cycles", ["project_id"], unique=False)
    op.create_index("ix_dbtl_cycles_projection_hash", "dbtl_cycles", ["projection_hash"], unique=False)
    op.create_index("ix_dbtl_cycles_project_state", "dbtl_cycles", ["project_id", "state"], unique=False)

    op.create_table(
        "dbtl_stage_runs",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("db_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cycle_id"], ["dbtl_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "stage", "attempt_number", name="uq_dbtl_stage_attempt"),
    )
    op.create_index("ix_dbtl_stage_runs_project_id", "dbtl_stage_runs", ["project_id"], unique=False)
    op.create_index("ix_dbtl_stage_runs_cycle_id", "dbtl_stage_runs", ["cycle_id"], unique=False)
    op.create_index("ix_dbtl_stage_project_cycle", "dbtl_stage_runs", ["project_id", "cycle_id"], unique=False)

    op.create_table(
        "dbtl_artifacts",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("stage_attempt_id", sa.String(length=96), nullable=False),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cycle_id"], ["dbtl_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_attempt_id"], ["dbtl_stage_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stage_attempt_id", "artifact_type", "revision", name="uq_dbtl_artifact_revision"),
    )
    op.create_index("ix_dbtl_artifacts_project_id", "dbtl_artifacts", ["project_id"], unique=False)
    op.create_index("ix_dbtl_artifacts_cycle_id", "dbtl_artifacts", ["cycle_id"], unique=False)
    op.create_index("ix_dbtl_artifacts_stage_attempt_id", "dbtl_artifacts", ["stage_attempt_id"], unique=False)

    op.create_table(
        "dbtl_reviews",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("stage_attempt_id", sa.String(length=96), nullable=False),
        sa.Column("artifact_id", sa.String(length=96), nullable=False),
        sa.Column("artifact_revision", sa.Integer(), nullable=False),
        sa.Column("bound_db_revision", sa.Integer(), nullable=False),
        sa.Column("bound_stage_revision", sa.Integer(), nullable=False),
        sa.Column("bound_projection_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=96), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("reviewer_user_id", sa.String(length=64), nullable=False),
        sa.Column("reviewer_project_role", sa.String(length=24), nullable=False),
        sa.Column("authorization_reference", sa.String(length=160), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["dbtl_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cycle_id"], ["dbtl_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_attempt_id"], ["dbtl_stage_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_dbtl_review_idempotency"),
    )
    op.create_index("ix_dbtl_reviews_project_id", "dbtl_reviews", ["project_id"], unique=False)
    op.create_index("ix_dbtl_reviews_cycle_id", "dbtl_reviews", ["cycle_id"], unique=False)
    op.create_index("ix_dbtl_reviews_reviewer_user_id", "dbtl_reviews", ["reviewer_user_id"], unique=False)
    op.create_index("ix_dbtl_reviews_cycle_created", "dbtl_reviews", ["cycle_id", "created_at"], unique=False)

    op.create_table(
        "activity_events",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("actor_user_id", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cycle_id"], ["dbtl_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "sequence", name="uq_dbtl_event_sequence"),
    )
    op.create_index("ix_activity_events_project_id", "activity_events", ["project_id"], unique=False)
    op.create_index("ix_activity_events_cycle_id", "activity_events", ["cycle_id"], unique=False)

    op.create_table(
        "dbtl_transition_intents",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("expected_db_revision", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(length=48), nullable=False),
        sa.Column("proposed_state", sa.String(length=48), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cycle_id"], ["dbtl_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_dbtl_transition_intent_idempotency",
        ),
    )
    op.create_index(
        "ix_dbtl_transition_intents_project_id",
        "dbtl_transition_intents",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_dbtl_transition_intents_cycle_id",
        "dbtl_transition_intents",
        ["cycle_id"],
        unique=False,
    )

    op.create_table(
        "dbtl_transitions",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("transition_intent_id", sa.String(length=96), nullable=False),
        sa.Column("committed_db_revision", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(length=48), nullable=False),
        sa.Column("to_state", sa.String(length=48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cycle_id"], ["dbtl_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["transition_intent_id"],
            ["dbtl_transition_intents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transition_intent_id"),
    )
    op.create_index("ix_dbtl_transitions_project_id", "dbtl_transitions", ["project_id"], unique=False)
    op.create_index("ix_dbtl_transitions_cycle_id", "dbtl_transitions", ["cycle_id"], unique=False)

    op.create_table(
        "dbtl_gate_evaluations",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("transition_intent_id", sa.String(length=96), nullable=False),
        sa.Column("expected_db_revision", sa.Integer(), nullable=False),
        sa.Column("artifact_hashes", sa.JSON(), nullable=False),
        sa.Column("projection_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_tool_name", sa.String(length=96), nullable=False),
        sa.Column("policy_tool_version", sa.String(length=96), nullable=False),
        sa.Column("policy_input", sa.JSON(), nullable=False),
        sa.Column("policy_output", sa.JSON(), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cycle_id"], ["dbtl_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["transition_intent_id"],
            ["dbtl_transition_intents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dbtl_gate_evaluations_project_id",
        "dbtl_gate_evaluations",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_dbtl_gate_evaluations_cycle_id",
        "dbtl_gate_evaluations",
        ["cycle_id"],
        unique=False,
    )
    op.create_index(
        "ix_dbtl_gate_evaluations_transition_intent_id",
        "dbtl_gate_evaluations",
        ["transition_intent_id"],
        unique=False,
    )

    op.create_table(
        "work_items",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("owner_role", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("db_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cycle_id"], ["dbtl_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_work_items_project_id", "work_items", ["project_id"], unique=False)
    op.create_index("ix_work_items_cycle_id", "work_items", ["cycle_id"], unique=False)

    op.create_table(
        "memory_candidates",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=True),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cycle_id"], ["dbtl_cycles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_candidates_project_id", "memory_candidates", ["project_id"], unique=False)
    op.create_index("ix_memory_candidates_cycle_id", "memory_candidates", ["cycle_id"], unique=False)

    op.create_table(
        "knowledge_claims",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_claims_project_id", "knowledge_claims", ["project_id"], unique=False)

    op.create_table(
        "knowledge_promotions",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("memory_candidate_id", sa.String(length=96), nullable=False),
        sa.Column("knowledge_claim_id", sa.String(length=96), nullable=False),
        sa.Column("promoted_by", sa.String(length=64), nullable=False),
        sa.Column("authorization_reference", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["knowledge_claim_id"],
            ["knowledge_claims.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["memory_candidate_id"],
            ["memory_candidates.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_promotions_memory_candidate_id",
        "knowledge_promotions",
        ["memory_candidate_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_promotions_knowledge_claim_id",
        "knowledge_promotions",
        ["knowledge_claim_id"],
        unique=False,
    )

    op.create_table(
        "knowledge_links",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("from_claim_id", sa.String(length=96), nullable=False),
        sa.Column("to_claim_id", sa.String(length=96), nullable=False),
        sa.Column("relationship", sa.String(length=48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["from_claim_id"], ["knowledge_claims.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_claim_id"], ["knowledge_claims.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "from_claim_id",
            "to_claim_id",
            "relationship",
            name="uq_knowledge_link",
        ),
    )
    op.create_index("ix_knowledge_links_project_id", "knowledge_links", ["project_id"], unique=False)

    op.create_table(
        "dbtl_validations",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("database_backend", sa.String(length=24), nullable=False),
        sa.Column("technical_ready", sa.Boolean(), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "dbtl_cutover_decisions",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("validation_id", sa.String(length=96), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("approved_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["validation_id"], ["dbtl_validations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("validation_id"),
    )


def downgrade() -> None:
    op.drop_table("dbtl_cutover_decisions")
    op.drop_table("dbtl_validations")
    op.drop_index("ix_knowledge_links_project_id", table_name="knowledge_links")
    op.drop_table("knowledge_links")
    op.drop_index(
        "ix_knowledge_promotions_knowledge_claim_id",
        table_name="knowledge_promotions",
    )
    op.drop_index(
        "ix_knowledge_promotions_memory_candidate_id",
        table_name="knowledge_promotions",
    )
    op.drop_table("knowledge_promotions")
    op.drop_index("ix_knowledge_claims_project_id", table_name="knowledge_claims")
    op.drop_table("knowledge_claims")
    op.drop_index("ix_memory_candidates_cycle_id", table_name="memory_candidates")
    op.drop_index("ix_memory_candidates_project_id", table_name="memory_candidates")
    op.drop_table("memory_candidates")
    op.drop_index("ix_work_items_cycle_id", table_name="work_items")
    op.drop_index("ix_work_items_project_id", table_name="work_items")
    op.drop_table("work_items")
    op.drop_index(
        "ix_dbtl_gate_evaluations_transition_intent_id",
        table_name="dbtl_gate_evaluations",
    )
    op.drop_index("ix_dbtl_gate_evaluations_cycle_id", table_name="dbtl_gate_evaluations")
    op.drop_index("ix_dbtl_gate_evaluations_project_id", table_name="dbtl_gate_evaluations")
    op.drop_table("dbtl_gate_evaluations")
    op.drop_index("ix_dbtl_transitions_cycle_id", table_name="dbtl_transitions")
    op.drop_index("ix_dbtl_transitions_project_id", table_name="dbtl_transitions")
    op.drop_table("dbtl_transitions")
    op.drop_index(
        "ix_dbtl_transition_intents_cycle_id",
        table_name="dbtl_transition_intents",
    )
    op.drop_index(
        "ix_dbtl_transition_intents_project_id",
        table_name="dbtl_transition_intents",
    )
    op.drop_table("dbtl_transition_intents")
    op.drop_index("ix_activity_events_cycle_id", table_name="activity_events")
    op.drop_index("ix_activity_events_project_id", table_name="activity_events")
    op.drop_table("activity_events")
    op.drop_index("ix_dbtl_reviews_cycle_created", table_name="dbtl_reviews")
    op.drop_index("ix_dbtl_reviews_reviewer_user_id", table_name="dbtl_reviews")
    op.drop_index("ix_dbtl_reviews_cycle_id", table_name="dbtl_reviews")
    op.drop_index("ix_dbtl_reviews_project_id", table_name="dbtl_reviews")
    op.drop_table("dbtl_reviews")
    op.drop_index("ix_dbtl_artifacts_stage_attempt_id", table_name="dbtl_artifacts")
    op.drop_index("ix_dbtl_artifacts_cycle_id", table_name="dbtl_artifacts")
    op.drop_index("ix_dbtl_artifacts_project_id", table_name="dbtl_artifacts")
    op.drop_table("dbtl_artifacts")
    op.drop_index("ix_dbtl_stage_project_cycle", table_name="dbtl_stage_runs")
    op.drop_index("ix_dbtl_stage_runs_cycle_id", table_name="dbtl_stage_runs")
    op.drop_index("ix_dbtl_stage_runs_project_id", table_name="dbtl_stage_runs")
    op.drop_table("dbtl_stage_runs")
    op.drop_index("ix_dbtl_cycles_project_state", table_name="dbtl_cycles")
    op.drop_index("ix_dbtl_cycles_projection_hash", table_name="dbtl_cycles")
    op.drop_index("ix_dbtl_cycles_project_id", table_name="dbtl_cycles")
    op.drop_table("dbtl_cycles")
