"""Foundation: organizations, users, memberships, audit_logs, RLS, app role.

Revision ID: 0001
Revises:
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "adpilot_app"
TENANT = "nullif(current_setting('app.org_id', true), '')::uuid"


def _pk() -> sa.Column[Any]:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _created() -> sa.Column[Any]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    # Non-superuser, non-BYPASSRLS role the application connects as (or SET ROLEs to).
    op.execute(
        f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS;
          END IF;
        END $$;
        """
    )

    op.create_table(
        "organizations",
        _pk(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("plan", sa.String(50), nullable=False, server_default="free"),
        _created(),
    )
    op.create_table(
        "users",
        _pk(),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("auth_subject", sa.String(255), unique=True),
        _created(),
    )
    op.create_table(
        "memberships",
        _pk(),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        _created(),
        sa.UniqueConstraint("user_id", "organization_id"),
    )
    op.create_index("ix_memberships_organization_id", "memberships", ["organization_id"])
    op.create_table(
        "audit_logs",
        _pk(),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(100)),
        sa.Column("entity_id", sa.String(100)),
        sa.Column("before", postgresql.JSONB),
        sa.Column("after", postgresql.JSONB),
        sa.Column("request_id", sa.Text),
        _created(),
    )
    op.create_index("ix_audit_logs_organization_id", "audit_logs", ["organization_id"])

    # Row-Level Security. FORCE applies policies even to the table owner.
    for table, column in (
        ("organizations", "id"),
        ("memberships", "organization_id"),
        ("audit_logs", "organization_id"),
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({column} = {TENANT}) WITH CHECK ({column} = {TENANT})"
        )

    # Least privilege. audit_logs is insert-only (no UPDATE/DELETE).
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON organizations, memberships TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON users TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON audit_logs TO {APP_ROLE}")


def downgrade() -> None:
    # The cluster-wide role is intentionally kept; dropping tables removes its grants.
    for table in ("audit_logs", "memberships", "users", "organizations"):
        op.drop_table(table)
