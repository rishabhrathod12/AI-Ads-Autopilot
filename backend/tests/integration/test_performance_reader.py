"""SqlPerformanceReader on PostgreSQL, checked against the generator's independent numpy sums."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.application.sync_service import SyncService
from app.domain.ads.ids import entity_uuid
from app.domain.ads.models import DailyMetrics, DataSource, EntityType
from app.domain.kpi.metrics import Kpis, MetricTotals
from app.domain.kpi.periods import DateRange, aggregate_period
from app.domain.tenant import TenantContext
from app.infrastructure.db.ads_store import SqlAdsStore
from app.infrastructure.db.models import Organization
from app.infrastructure.db.performance_reader import SqlPerformanceReader
from app.infrastructure.db.session import tenant_session
from app.infrastructure.providers.mock.generator import MockConfig
from app.infrastructure.providers.mock.provider import MockAdvertisingDataProvider

pytestmark = pytest.mark.db
ROLE = "adpilot_app"
END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
CFG = MockConfig(
    days=30,
    end_date=END,
    n_campaigns=6,
    ad_groups_per_campaign=2,
    keywords_per_ad_group=8,
    now=NOW,
)
FULL = DateRange(END - timedelta(days=29), END)
PROVIDER = MockAdvertisingDataProvider(CFG)


async def seed_org(engine: AsyncEngine) -> UUID:
    org = uuid4()
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        s.add(Organization(id=org, name="Reader Test"))
        await s.flush()
        await SyncService(PROVIDER, SqlAdsStore(s, org)).sync(FULL.start, FULL.end)
    return org


def reader_for(session: AsyncSession, org: UUID) -> SqlPerformanceReader:
    return SqlPerformanceReader(session, org)


def expected_by_entity(etype: EntityType, org: UUID) -> dict[UUID, dict[str, int]]:
    t = PROVIDER.data.metrics[etype.value]
    out: dict[UUID, dict[str, int]] = {}
    for i, ext in enumerate(t.ids):
        m = t.entity == i
        out[entity_uuid(org, etype.value, ext)] = {
            "impressions": int(t.impressions[m].sum()),
            "clicks": int(t.clicks[m].sum()),
            "spend_paise": int(t.spend_paise[m].sum()),
            "sales_paise": int(t.sales_paise[m].sum()),
            "orders": int(t.orders[m].sum()),
            "units": int(t.units[m].sum()),
            "days": int(m.sum()),
        }
    return out


@pytest.mark.parametrize("etype", list(EntityType))
async def test_totals_by_entity_match_independent_sums(
    engine: AsyncEngine, etype: EntityType
) -> None:
    org = await seed_org(engine)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        got = await reader_for(s, org).totals_by_entity(etype, FULL)
    expected = expected_by_entity(etype, org)
    assert {g.entity_id for g in got} == {k for k, v in expected.items() if v["days"] > 0}
    for g in got:
        e = expected[g.entity_id]
        assert g.totals.clicks == e["clicks"]
        assert g.totals.spend_paise == e["spend_paise"]
        assert g.totals.sales_paise == e["sales_paise"]
        assert g.totals.orders == e["orders"]
        assert g.active_days == e["days"]
    assert [g.entity_id for g in got] == sorted(g.entity_id for g in got)


async def test_maturity_filter_excludes_the_attribution_window(engine: AsyncEngine) -> None:
    org = await seed_org(engine)
    t = PROVIDER.data.metrics["campaign"]
    mature_idx = PROVIDER.data.mature_through
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        r = reader_for(s, org)
        everything = await r.totals_by_entity(EntityType.CAMPAIGN, FULL, include_immature=True)
        mature = await r.totals_by_entity(EntityType.CAMPAIGN, FULL, include_immature=False)
    assert sum(g.totals.clicks for g in everything) == int(t.clicks.sum())
    assert sum(g.totals.clicks for g in mature) == int(t.clicks[t.day <= mature_idx].sum())
    assert sum(g.totals.orders for g in mature) == int(t.orders[t.day <= mature_idx].sum())
    assert all(g.immature_days == 0 for g in mature)
    assert all(g.immature_days == 3 for g in everything)  # last 3 days are inside the window
    assert sum(g.totals.orders for g in mature) <= sum(g.totals.orders for g in everything)


async def test_daily_totals_match_and_flag_maturity(engine: AsyncEngine) -> None:
    org = await seed_org(engine)
    t = PROVIDER.data.metrics["campaign"]
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        series = await reader_for(s, org).daily_totals(EntityType.CAMPAIGN, FULL)
    assert [p.day for p in series] == sorted(p.day for p in series)
    for p in series:
        idx = (p.day - PROVIDER.data.dates[0]).days
        assert p.totals.spend_paise == int(t.spend_paise[t.day == idx].sum())
        assert p.is_mature == (idx <= PROVIDER.data.mature_through)


async def test_period_and_entity_filters(engine: AsyncEngine) -> None:
    org = await seed_org(engine)
    week = DateRange(END - timedelta(days=13), END - timedelta(days=7))
    one = entity_uuid(org, "campaign", "MOCK-CMP-000")
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        r = reader_for(s, org)
        sliced = await r.daily_totals(EntityType.CAMPAIGN, week)
        only_one = await r.daily_by_entity(EntityType.CAMPAIGN, week, [one])
        none = await r.totals_by_entity(EntityType.CAMPAIGN, FULL, entity_ids=[])
        absent = await r.totals_by_entity(EntityType.CAMPAIGN, FULL, entity_ids=[uuid4()])
    assert sliced[0].day == week.start
    assert sliced[-1].day == week.end
    assert {p.entity_id for p in only_one} == {one}
    assert [p.day for p in only_one] == sorted(p.day for p in only_one)
    assert len(only_one) == week.days
    assert none == []
    assert absent == []


async def test_reader_kpis_agree_with_domain_engine_over_provider_rows(
    engine: AsyncEngine,
) -> None:
    org = await seed_org(engine)
    rows: list[DailyMetrics] = []
    async for chunk in PROVIDER.fetch_daily_metrics(
        "MOCK-PRF-001", EntityType.CAMPAIGN, FULL.start, FULL.end
    ):
        rows.extend(chunk)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        series = await reader_for(s, org).daily_totals(EntityType.CAMPAIGN, FULL)
    from_db = Kpis.from_totals(MetricTotals.sum(p.totals for p in series))
    from_domain = aggregate_period(rows, FULL).kpis
    assert from_db == from_domain


async def test_freshness_and_provenance(engine: AsyncEngine) -> None:
    org = await seed_org(engine)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        r = reader_for(s, org)
        fresh = await r.freshness()
        prov = await r.provenance(FULL)
        campaigns = await r.campaigns()
    assert fresh.latest_date == END
    assert fresh.mature_through == END - timedelta(days=3)
    assert prov.data_sources == frozenset({DataSource.MOCK})
    assert prov.as_of == NOW
    assert len(campaigns) == 6
    assert all(c.data_source is DataSource.MOCK and c.daily_budget_paise > 0 for c in campaigns)
    assert [c.external_id for c in campaigns] == sorted(c.external_id for c in campaigns)


async def test_empty_tenant_returns_empty_results_not_errors(engine: AsyncEngine) -> None:
    org = uuid4()
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        s.add(Organization(id=org, name="Empty"))
        await s.flush()
        r = reader_for(s, org)
        assert (await r.freshness()).latest_date is None
        assert (await r.provenance(FULL)).data_sources == frozenset()
        assert await r.campaigns() == []
        assert await r.daily_totals(EntityType.CAMPAIGN, FULL) == []
        assert await r.totals_by_entity(EntityType.SEARCH_TERM, FULL) == []


async def test_tenant_isolation_holds_even_if_the_reader_asks_for_another_org(
    engine: AsyncEngine,
) -> None:
    org_a, org_b = await seed_org(engine), uuid4()
    async with tenant_session(engine, TenantContext(org_b), ROLE) as s:
        s.add(Organization(id=org_b, name="B"))
        await s.flush()
        # Wrong-org reader on B's transaction: the explicit filter AND RLS both say "nothing".
        leaky = reader_for(s, org_a)
        assert await leaky.totals_by_entity(EntityType.CAMPAIGN, FULL) == []
        assert await leaky.campaigns() == []
        assert (await leaky.freshness()).latest_date is None
        # Right-org reader sees nothing either: B has no data.
        assert await reader_for(s, org_b).totals_by_entity(EntityType.CAMPAIGN, FULL) == []


async def test_analytics_services_give_identical_results_on_sql_and_in_memory_readers(
    engine: AsyncEngine,
) -> None:
    from app.application.analytics.campaigns import campaign_changes, campaign_overview
    from app.application.analytics.dashboard import dashboard_summary
    from tests.unit.fake_reader import FakeReader

    org = await seed_org(engine)
    fake = FakeReader(PROVIDER.data, org, NOW)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        sql = reader_for(s, org)
        assert await dashboard_summary(sql) == await dashboard_summary(fake)
        period = DateRange(END - timedelta(days=20), END)
        assert await campaign_overview(sql, period) == await campaign_overview(fake, period)
        assert await campaign_changes(sql, period) == await campaign_changes(fake, period)


async def test_keyword_analysis_and_catalogs_match_between_sql_and_in_memory_readers(
    engine: AsyncEngine,
) -> None:
    from app.application.analytics.keywords import keyword_analysis
    from tests.unit.fake_reader import FakeReader

    org = await seed_org(engine)
    fake = FakeReader(PROVIDER.data, org, NOW)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        sql = reader_for(s, org)
        assert await sql.targets() == await fake.targets()
        assert await sql.search_terms() == await fake.search_terms()
        period = DateRange(END - timedelta(days=28), END)
        assert await keyword_analysis(sql, period) == await keyword_analysis(fake, period)


async def test_catalogs_are_tenant_isolated(engine: AsyncEngine) -> None:
    org_a, org_b = await seed_org(engine), uuid4()
    async with tenant_session(engine, TenantContext(org_b), ROLE) as s:
        s.add(Organization(id=org_b, name="B"))
        await s.flush()
        assert await reader_for(s, org_a).targets() == []
        assert await reader_for(s, org_a).search_terms() == []
        assert await reader_for(s, org_b).targets() == []


async def test_anomaly_report_matches_between_sql_and_in_memory_readers(
    engine: AsyncEngine,
) -> None:
    from app.application.analytics.anomalies import detect_anomalies
    from tests.unit.fake_reader import FakeReader

    cfg = MockConfig(
        days=90,
        end_date=END,
        n_campaigns=6,
        ad_groups_per_campaign=2,
        keywords_per_ad_group=20,
        now=NOW,
    )
    provider = MockAdvertisingDataProvider(cfg)
    org = uuid4()
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        s.add(Organization(id=org, name="Anomaly parity"))
        await s.flush()
        await SyncService(provider, SqlAdsStore(s, org)).sync(END - timedelta(days=89), END)
        sql_report = await detect_anomalies(reader_for(s, org))
    fake_report = await detect_anomalies(FakeReader(provider.data, org, NOW))
    assert sql_report == fake_report
    assert sql_report.skipped == []  # 90 days is enough history to monitor every campaign
