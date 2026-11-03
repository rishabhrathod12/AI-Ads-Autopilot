"""Assistant: threads, messages, immutable run traces and the LLM spend ledger.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = "adpilot_app"
TENANT = "nullif(current_setting('app.org_id', true), '')::uuid"
UUID = postgresql.UUID(as_uuid=True)
NOW = sa.text("clock_timestamp()")  # not now(): rows in one transaction must not tie
TABLES = ["assistant_threads", "assistant_runs", "assistant_messages", "llm_usage"]
STATUSES = (
    "'answered','partial','unverified','step_limit','budget_exceeded',"
    "'llm_unavailable','llm_error','refused','not_configured'"
)


def _org() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False)


def _created() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False)


def upgrade() -> None:
    op.create_table(
        "assistant_threads",
        sa.Column("id", UUID, primary_key=True),
        _org(),
        sa.Column("title", sa.String(200), nullable=False),
        _created(),
    )
    op.create_index(
        "ix_assistant_threads_organization_id", "assistant_threads", ["organization_id"]
    )

    op.create_table(
        "assistant_runs",
        sa.Column("id", UUID, primary_key=True),
        _org(),
        sa.Column("thread_id", UUID, sa.ForeignKey("assistant_threads.id"), nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("answer", postgresql.JSONB, nullable=False),
        sa.Column("evidence", postgresql.JSONB, nullable=False),
        sa.Column("trace", postgresql.JSONB, nullable=False),
        sa.Column("violations", postgresql.JSONB, nullable=False),
        sa.Column("flags", postgresql.JSONB, nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("llm_calls", sa.Integer, nullable=False),
        sa.Column("tool_calls", sa.Integer, nullable=False),
        sa.Column("validation_attempts", sa.Integer, nullable=False),
        _created(),
        sa.CheckConstraint(f"status IN ({STATUSES})"),
        sa.CheckConstraint("input_tokens >= 0 AND output_tokens >= 0 AND cost_usd >= 0"),
    )
    op.create_index("ix_assistant_runs_organization_id", "assistant_runs", ["organization_id"])
    op.create_index("ix_assistant_runs_thread", "assistant_runs", ["thread_id", "created_at"])

    op.create_table(
        "assistant_messages",
        sa.Column("id", UUID, primary_key=True),
        _org(),
        sa.Column("thread_id", UUID, sa.ForeignKey("assistant_threads.id"), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("run_id", UUID, sa.ForeignKey("assistant_runs.id")),
        _created(),
        sa.CheckConstraint("role IN ('user','assistant')"),
    )
    op.create_index(
        "ix_assistant_messages_organization_id", "assistant_messages", ["organization_id"]
    )
    op.create_index(
        "ix_assistant_messages_thread", "assistant_messages", ["thread_id", "created_at"]
    )

    op.create_table(
        "llm_usage",
        sa.Column("id", UUID, primary_key=True),
        _org(),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        _created(),
        sa.CheckConstraint("cost_usd >= 0"),
    )
    op.create_index("ix_llm_usage_organization_id", "llm_usage", ["organization_id"])
    op.create_index("ix_llm_usage_org_time", "llm_usage", ["organization_id", "created_at"])

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (organization_id = {TENANT}) WITH CHECK (organization_id = {TENANT})"
        )
        # Immutable history: no UPDATE, no DELETE. A conversation is an audit trail.
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    for table in ("llm_usage", "assistant_messages", "assistant_runs", "assistant_threads"):
        op.drop_table(table)
