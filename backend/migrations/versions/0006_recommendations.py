"""Recommendations, approval records, execution attempts (M6).

Revision ID: 0006
Revises: 0005

Verified 2026-11-12 against PostgreSQL 18.6 with pgvector 0.8.7 (a throwaway local cluster).
Not yet run on PostgreSQL 16, the CI image. Upgrade, downgrade
and the match against the ORM models are covered by the integration tests.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

APP_ROLE = "adpilot_app"
TENANT = "nullif(current_setting('app.org_id', true), '')::uuid"
UUID = postgresql.UUID(as_uuid=True)


def _org() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "recommendations",
        sa.Column("id", UUID, primary_key=True),
        _org(),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("entity_id", sa.String(100), nullable=False),
        sa.Column("entity_label", sa.String(500), nullable=False),
        sa.Column("current_value", sa.BigInteger),
        sa.Column("proposed_value", sa.BigInteger),
        sa.Column("proposed_text", sa.Text),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("evidence", postgresql.JSONB, nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("expected_impact", sa.Text, nullable=False),
        sa.Column("data_source", sa.String(30), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "content_hash", name="uq_recommendations_org_hash"),
        sa.CheckConstraint("type IN ('bid_change', 'budget_change', 'negative_keyword')"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'expired')"),
        sa.CheckConstraint("data_source IN ('MOCK', 'AMAZON_ADS_API')"),
        sa.CheckConstraint("expires_at > created_at"),
    )
    op.create_index("ix_recommendations_organization_id", "recommendations", ["organization_id"])

    op.create_table(
        "approval_records",
        sa.Column("id", UUID, primary_key=True),
        _org(),
        sa.Column(
            "recommendation_id",
            UUID,
            sa.ForeignKey("recommendations.id"),
            nullable=False,
            unique=True,  # at most one decision per recommendation
        ),
        sa.Column("decided_by", sa.String(255), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("comment", sa.Text),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision IN ('approve', 'reject')"),
    )
    op.create_index("ix_approval_records_organization_id", "approval_records", ["organization_id"])

    op.create_table(
        "execution_attempts",
        sa.Column("id", UUID, primary_key=True),
        _org(),
        sa.Column("recommendation_id", UUID, sa.ForeignKey("recommendations.id"), nullable=False),
        sa.Column("approval_id", UUID, sa.ForeignKey("approval_records.id")),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("dry_run", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('blocked')"),
    )
    op.create_index(
        "ix_execution_attempts_organization_id", "execution_attempts", ["organization_id"]
    )
    op.create_index(
        "ix_execution_attempts_recommendation_id", "execution_attempts", ["recommendation_id"]
    )

    for table in ("recommendations", "approval_records", "execution_attempts"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (organization_id = {TENANT}) WITH CHECK (organization_id = {TENANT})"
        )

    # Least privilege. Only a recommendation's status may change, and only through UPDATE(status);
    # approvals and execution attempts can be read and added, never changed or removed.
    op.execute(f"GRANT SELECT, INSERT ON recommendations TO {APP_ROLE}")
    op.execute(f"GRANT UPDATE (status) ON recommendations TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON approval_records TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON execution_attempts TO {APP_ROLE}")


def downgrade() -> None:
    for table in ("execution_attempts", "approval_records", "recommendations"):
        op.drop_table(table)
