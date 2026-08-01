"""Phase 8 Learn, selected-project publication, and immutable knowledge events.

Revision ID: 0017_dbtl_learn_knowledge
Revises: 0016_dbtl_build_test_validity
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_dbtl_learn_knowledge"
down_revision = "0016_dbtl_build_test_validity"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "knowledge_claims" not in tables:
        raise RuntimeError(
            "Migration 0017 requires the knowledge_claims table from the DBTL governance foundation; "
            "refusing to stamp an incomplete knowledge schema."
        )
    if "knowledge_publications" not in tables:
        op.create_table(
            "knowledge_publications",
            sa.Column("id", sa.String(length=96), nullable=False),
            sa.Column(
                "claim_id",
                sa.String(length=96),
                sa.ForeignKey("knowledge_claims.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "source_project_id",
                sa.String(length=64),
                sa.ForeignKey("projects.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "target_project_id",
                sa.String(length=64),
                sa.ForeignKey("projects.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("pointer", sa.JSON(), nullable=False),
            sa.Column("published_by", sa.String(length=64), nullable=False),
            sa.Column("publisher_project_role", sa.String(length=24), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("authorization_reference", sa.String(length=160), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("retracted_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "claim_id",
                "target_project_id",
                name="uq_knowledge_publication_target",
            ),
        )
        for column in ("claim_id", "source_project_id", "target_project_id"):
            op.create_index(
                f"ix_knowledge_publications_{column}",
                "knowledge_publications",
                [column],
            )
    if "knowledge_events" not in tables:
        op.create_table(
            "knowledge_events",
            sa.Column("id", sa.String(length=96), nullable=False),
            sa.Column(
                "project_id",
                sa.String(length=64),
                sa.ForeignKey("projects.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "cycle_id",
                sa.String(length=64),
                sa.ForeignKey("dbtl_cycles.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "candidate_id",
                sa.String(length=96),
                sa.ForeignKey("memory_candidates.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "claim_id",
                sa.String(length=96),
                sa.ForeignKey("knowledge_claims.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "publication_id",
                sa.String(length=96),
                sa.ForeignKey("knowledge_publications.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("event_type", sa.String(length=48), nullable=False),
            sa.Column("actor_user_id", sa.String(length=64), nullable=False),
            sa.Column("idempotency_key", sa.String(length=128), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "project_id",
                "idempotency_key",
                name="uq_knowledge_event_idempotency",
            ),
        )
        for column in (
            "project_id",
            "cycle_id",
            "candidate_id",
            "claim_id",
            "publication_id",
        ):
            op.create_index(f"ix_knowledge_events_{column}", "knowledge_events", [column])


def downgrade() -> None:
    tables = _tables()
    if "knowledge_events" in tables:
        op.drop_table("knowledge_events")
    if "knowledge_publications" in tables:
        op.drop_table("knowledge_publications")
