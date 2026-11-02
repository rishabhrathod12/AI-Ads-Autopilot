"""MOCK sync into PostgreSQL: persistence, idempotency, provenance, schema parity and RLS."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.sync_service import SyncResult, SyncService
from app.domain.tenant import TenantContext
from app.infrastructure.db import ads_models as m
from app.infrastructure.db.ads_store import SqlAdsStore
from app.infrastructure.db.models import Base, Organization
from app.infrastructure.db.session import tenant_session
from app.infrastructure.providers.mock.generator import MockConfig
from app.infrastructure.providers.mock.provider import MockAdvertisingDataProvider

pytestmark = pytest.mark.db
ROLE = "adpilot_app"
END = date(2026, 10, 31)
CFG = MockConfig(
    days=30,
    end_date=END,
    n_campaigns=6,
    ad_groups_per_campaign=2,
    keywords_per_ad_group=8,
    now=datetime(2026, 11, 1, tzinfo=UTC),
)
TABLES = [m.AdvertisingProfile, m.Campaign, m.AdGroup, m.KeywordTarget, m.SearchTermRow]


async def sync_org(engine: AsyncEngine, org_id: UUID) -> SyncResult:
    provider = MockAdvertisingDataProvider(CFG)
    async with tenant_session(engine, TenantContext(org_id), ROLE) as s:
        if await s.scalar(select(Organization.id).where(Organization.id == org_id)) is None:
            s.add(Organization(id=org_id, name="Test Org"))
            await s.flush()
        return await SyncService(provider, SqlAdsStore(s, org_id)).sync(
            END - timedelta(days=29), END
        )


async def count(engine: AsyncEngine, org_id: UUID, model: type[Base]) -> int:
    async with tenant_session(engine, TenantContext(org_id), ROLE) as s:
        return int(await s.scalar(select(func.count()).select_from(model)) or 0)


async def test_migrations_match_orm_models(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        diffs = await conn.run_sync(
            lambda c: compare_metadata(MigrationContext.configure(c), Base.metadata)
        )
    assert diffs == []


async def test_sync_persists_mock_data_with_provenance(engine: AsyncEngine) -> None:
    org = uuid4()
    result = await sync_org(engine, org)
    d = MockAdvertisingDataProvider(CFG).data
    assert result.rows_quarantined == 0
    assert await count(engine, org, m.Campaign) == len(d.campaigns)
    assert await count(engine, org, m.KeywordTarget) == len(d.targets)
    assert await count(engine, org, m.SearchTermRow) == len(d.search_terms)
    assert await count(engine, org, m.PerformanceDaily) == result.metrics_rows

    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        sources = (
            await s.execute(
                text(
                    "SELECT 'campaigns', data_source FROM campaigns UNION ALL "
                    "SELECT 'performance', data_source FROM performance_daily UNION ALL "
                    "SELECT 'runs', data_source FROM report_runs"
                )
            )
        ).all()
        runs = (await s.scalars(select(m.ReportRun))).all()
    assert {r[1] for r in sources} == {"MOCK"}
    assert len(runs) == 4
    assert {r.status for r in runs} == {"completed"}
    assert sum(r.rows_ingested for r in runs) == result.metrics_rows


async def test_resync_is_idempotent(engine: AsyncEngine) -> None:
    org = uuid4()
    await sync_org(engine, org)
    before = {t.__name__: await count(engine, org, t) for t in [*TABLES, m.PerformanceDaily]}
    await sync_org(engine, org)
    after = {t.__name__: await count(engine, org, t) for t in [*TABLES, m.PerformanceDaily]}
    assert before == after
    assert await count(engine, org, m.ReportRun) == 8  # runs accumulate: that is the audit trail


async def test_campaign_totals_equal_sum_of_ad_groups_in_database(engine: AsyncEngine) -> None:
    org = uuid4()
    await sync_org(engine, org)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        totals = dict(
            (
                await s.execute(
                    select(
                        m.PerformanceDaily.entity_type, func.sum(m.PerformanceDaily.spend_paise)
                    ).group_by(m.PerformanceDaily.entity_type)
                )
            ).all()
        )
    assert len({int(v) for v in totals.values()}) == 1, totals


async def test_other_tenant_sees_nothing_and_cannot_write_into_it(engine: AsyncEngine) -> None:
    org_a, org_b = uuid4(), uuid4()
    await sync_org(engine, org_a)
    async with tenant_session(engine, TenantContext(org_b), ROLE) as s:
        s.add(Organization(id=org_b, name="B"))
    for model in [*TABLES, m.PerformanceDaily, m.ReportRun, m.SellerAccount]:
        assert await count(engine, org_b, model) == 0, model.__name__

    with pytest.raises(DBAPIError):  # WITH CHECK blocks writing a row owned by org A
        async with tenant_session(engine, TenantContext(org_b), ROLE) as s:
            s.add(
                m.SellerAccount(
                    id=uuid4(),
                    organization_id=org_a,
                    marketplace="x",
                    region="FE",
                    provider_type="MOCK",
                )
            )
            await s.flush()


async def test_app_role_cannot_delete_synced_history(engine: AsyncEngine) -> None:
    org = uuid4()
    await sync_org(engine, org)
    for table in ("performance_daily", "campaigns", "report_runs"):
        with pytest.raises(ProgrammingError):
            async with tenant_session(engine, TenantContext(org), ROLE) as s:
                await s.execute(text(f"DELETE FROM {table}"))  # noqa: S608


async def test_database_rejects_impossible_metrics(engine: AsyncEngine) -> None:
    org = uuid4()
    await sync_org(engine, org)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        any_row = (await s.scalars(select(m.PerformanceDaily).limit(1))).one()
        run_id = any_row.report_run_id
    with pytest.raises(DBAPIError):
        async with tenant_session(engine, TenantContext(org), ROLE) as s:
            s.add(
                m.PerformanceDaily(
                    organization_id=org,
                    entity_type="campaign",
                    entity_id=uuid4(),
                    date=END,
                    impressions=1,
                    clicks=5,  # more clicks than impressions
                    spend_paise=0,
                    sales_paise=0,
                    orders=0,
                    units=0,
                    attribution_days=7,
                    is_mature=True,
                    report_run_id=run_id,
                    data_source="MOCK",
                    ingested_at=datetime.now(UTC),
                )
            )
            await s.flush()
