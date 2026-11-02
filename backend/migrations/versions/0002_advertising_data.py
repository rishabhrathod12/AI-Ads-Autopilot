"""Advertising data: accounts, profiles, campaigns, ad groups, targets, terms, reports, metrics.

Revision ID: 0002
Revises: 0001
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = "adpilot_app"
TENANT = "nullif(current_setting('app.org_id', true), '')::uuid"
SOURCES = "data_source IN ('MOCK', 'AMAZON_ADS_API')"
UUID = postgresql.UUID(as_uuid=True)
TABLES = [
    "seller_accounts",
    "advertising_profiles",
    "campaigns",
    "ad_groups",
    "keywords_targets",
    "search_terms",
    "report_runs",
    "performance_daily",
]


def _id() -> sa.Column[Any]:
    return sa.Column("id", UUID, primary_key=True)


def _org() -> sa.Column[Any]:
    return sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False)


def _ref(name: str, table: str, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(name, UUID, sa.ForeignKey(f"{table}.id"), nullable=nullable)


def _prov() -> list[sa.Column[Any]]:
    return [
        sa.Column("data_source", sa.String(30), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _created() -> sa.Column[Any]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _entity(name: str, *cols: Any) -> None:
    op.create_table(
        name,
        _id(),
        _org(),
        sa.Column("external_id", sa.String(64), nullable=False),
        *cols,
        *_prov(),
        _created(),
        sa.UniqueConstraint("organization_id", "external_id"),
        sa.CheckConstraint(SOURCES),
    )
    op.create_index(f"ix_{name}_organization_id", name, ["organization_id"])


def upgrade() -> None:
    op.create_table(
        "seller_accounts",
        _id(),
        _org(),
        sa.Column("marketplace", sa.String(50), nullable=False),
        sa.Column("region", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("provider_type", sa.String(30), nullable=False),
        _created(),
        sa.CheckConstraint(SOURCES.replace("data_source", "provider_type")),
    )
    op.create_index("ix_seller_accounts_organization_id", "seller_accounts", ["organization_id"])

    _entity(
        "advertising_profiles",
        _ref("seller_account_id", "seller_accounts"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
    )
    _entity(
        "campaigns",
        _ref("profile_id", "advertising_profiles"),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("targeting_type", sa.String(20), nullable=False),
        sa.Column("daily_budget_paise", sa.BigInteger, nullable=False),
        sa.Column("bidding_strategy", sa.String(100), nullable=False),
    )
    _entity(
        "ad_groups",
        _ref("campaign_id", "campaigns"),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("default_bid_paise", sa.BigInteger, nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
    )
    _entity(
        "keywords_targets",
        _ref("ad_group_id", "ad_groups"),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("match_type", sa.String(20), nullable=False),
        sa.Column("bid_paise", sa.BigInteger, nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("is_negative", sa.Boolean, nullable=False, server_default="false"),
    )
    _entity(
        "search_terms",
        _ref("ad_group_id", "ad_groups"),
        _ref("target_id", "keywords_targets", nullable=True),
        sa.Column("term", sa.String(500), nullable=False),
    )

    op.create_table(
        "report_runs",
        _id(),
        _org(),
        _ref("profile_id", "advertising_profiles"),
        sa.Column("report_type", sa.String(50), nullable=False),
        sa.Column("date_start", sa.Date, nullable=False),
        sa.Column("date_end", sa.Date, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("rows_ingested", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rows_quarantined", sa.Integer, nullable=False, server_default="0"),
        *_prov(),
        _created(),
        sa.CheckConstraint(SOURCES),
    )
    op.create_index("ix_report_runs_organization_id", "report_runs", ["organization_id"])

    op.create_table(
        "performance_daily",
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", UUID, nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("impressions", sa.Integer, nullable=False),
        sa.Column("clicks", sa.Integer, nullable=False),
        sa.Column("spend_paise", sa.BigInteger, nullable=False),
        sa.Column("sales_paise", sa.BigInteger, nullable=False),
        sa.Column("orders", sa.Integer, nullable=False),
        sa.Column("units", sa.Integer, nullable=False),
        sa.Column("attribution_days", sa.Integer, nullable=False),
        sa.Column("is_mature", sa.Boolean, nullable=False),
        _ref("report_run_id", "report_runs"),
        *_prov(),
        sa.PrimaryKeyConstraint("organization_id", "entity_type", "entity_id", "date"),
        sa.CheckConstraint(SOURCES),
        sa.CheckConstraint("clicks <= impressions AND orders <= clicks AND units >= orders"),
        sa.CheckConstraint(
            "impressions >= 0 AND clicks >= 0 AND spend_paise >= 0 AND sales_paise >= 0"
        ),
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (organization_id = {TENANT}) WITH CHECK (organization_id = {TENANT})"
        )
        # Least privilege: no DELETE. Syncs upsert; history is retained.
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
