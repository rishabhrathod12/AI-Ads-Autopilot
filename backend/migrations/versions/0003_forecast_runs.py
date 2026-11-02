"""Forecast runs: stored, immutable forecasts with the evidence that selected the model.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APP_ROLE = "adpilot_app"
TENANT = "nullif(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "forecast_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True)),
        sa.Column("metric", sa.String(20), nullable=False),
        sa.Column("horizon_start", sa.Date, nullable=False),
        sa.Column("horizon_end", sa.Date, nullable=False),
        sa.Column("train_start", sa.Date, nullable=False),
        sa.Column("train_end", sa.Date, nullable=False),
        sa.Column("train_days", sa.Integer, nullable=False),
        sa.Column("model", sa.String(50), nullable=False),
        sa.Column("used_baseline", sa.Boolean, nullable=False),
        sa.Column("params", postgresql.JSONB, nullable=False),
        sa.Column("points", postgresql.JSONB, nullable=False),
        sa.Column("data_source", sa.String(30), nullable=False),
        sa.Column("input_as_of", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),  # not now(): see ForecastRun
            nullable=False,
        ),
        sa.CheckConstraint("scope_type IN ('account', 'campaign')"),
        sa.CheckConstraint("metric IN ('spend', 'sales')"),
        sa.CheckConstraint("data_source IN ('MOCK', 'AMAZON_ADS_API')"),
        sa.CheckConstraint(
            "(scope_type = 'account' AND scope_id IS NULL) "
            "OR (scope_type = 'campaign' AND scope_id IS NOT NULL)"
        ),
        sa.CheckConstraint("horizon_end >= horizon_start AND train_end >= train_start"),
    )
    op.create_index("ix_forecast_runs_organization_id", "forecast_runs", ["organization_id"])

    op.execute("ALTER TABLE forecast_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE forecast_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON forecast_runs "
        f"USING (organization_id = {TENANT}) WITH CHECK (organization_id = {TENANT})"
    )
    # Immutable: a re-forecast is a new row, so no UPDATE and no DELETE.
    op.execute(f"GRANT SELECT, INSERT ON forecast_runs TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("forecast_runs")
