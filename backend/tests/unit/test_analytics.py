"""Dashboard + campaign analytics over an in-memory reader, scored against planted ground truth."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.application.analytics.campaigns import (
    CampaignOverview,
    CampaignRow,
    HealthStatus,
    assess_budget,
    assess_health,
    campaign_changes,
    campaign_overview,
)
from app.application.analytics.dashboard import NoDataError, dashboard_summary
from app.application.analytics.thresholds import MIN_CLICKS_FOR_HEALTH
from app.application.ports.performance_reader import Freshness
from app.domain.ads.models import DataSource
from app.domain.kpi.metrics import Kpis, MetricTotals, acos_pct
from app.domain.kpi.periods import DateRange, NoMatureDataError
from app.infrastructure.providers.mock.generator import GeneratedData, MockConfig, generate
from tests.unit.fake_reader import FakeReader

D = Decimal
END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
ORG = UUID("22222222-2222-4222-8222-222222222222")
LAST_28 = DateRange(END - timedelta(days=27), END)


@pytest.fixture(scope="module")
def data() -> GeneratedData:
    return generate(MockConfig(end_date=END, now=NOW))


@pytest.fixture(scope="module")
def reader(data: GeneratedData) -> FakeReader:
    return FakeReader(data, ORG, NOW)


def ext(rows: list[CampaignRow], status: HealthStatus) -> set[str]:
    return {r.info.external_id for r in rows if r.health.status is status}


# ---- dashboard -----------------------------------------------------------------------------
async def test_default_summary_covers_the_last_28_days_and_flags_immature_days(
    reader: FakeReader, data: GeneratedData
) -> None:
    s = await dashboard_summary(reader)
    assert s.period == LAST_28
    assert len(s.trend) == 28
    assert s.immature_days == 3
    assert [p.is_mature for p in s.trend[-4:]] == [True, False, False, False]
    assert s.kpis == Kpis.from_totals(s.totals)
    t = data.metrics["campaign"]
    first = (LAST_28.start - data.dates[0]).days
    assert s.totals.spend_paise == int(t.spend_paise[t.day >= first].sum())  # all days shown
    assert s.provenance.data_sources == {DataSource.MOCK}
    assert s.provenance.as_of == NOW
    assert s.freshness.mature_through == END - timedelta(days=3)


async def test_comparison_uses_mature_days_only_and_is_aligned(
    reader: FakeReader, data: GeneratedData
) -> None:
    c = (await dashboard_summary(reader)).comparison
    assert c is not None
    assert c.truncated_days == 3
    assert c.current == DateRange(LAST_28.start, END - timedelta(days=3))
    assert c.previous.days == c.current.days
    assert c.previous.end == c.current.start - timedelta(days=1)
    t = data.metrics["campaign"]
    lo = (c.current.start - data.dates[0]).days
    hi = (c.current.end - data.dates[0]).days
    assert c.current_totals.spend_paise == int(t.spend_paise[(t.day >= lo) & (t.day <= hi)].sum())


async def test_attribution_lag_would_distort_the_latest_days_if_not_excluded(
    reader: FakeReader,
) -> None:
    s = await dashboard_summary(reader)
    mature_acos = [acos_pct(p.totals.spend_paise, p.totals.sales_paise) for p in s.trend[:-3]]
    latest = acos_pct(s.trend[-1].totals.spend_paise, s.trend[-1].totals.sales_paise)
    assert latest is not None
    typical = sorted(a for a in mature_acos if a is not None)[len(mature_acos) // 2]
    assert latest > D("1.3") * typical  # looks ~1.8x worse purely because sales are not in yet


async def test_fully_immature_period_has_no_comparison_but_still_shows_totals(
    reader: FakeReader,
) -> None:
    s = await dashboard_summary(reader, DateRange(END - timedelta(days=1), END))
    assert s.comparison is None
    assert s.totals.clicks > 0
    assert s.immature_days == 2


async def test_empty_account_raises_no_data(data: GeneratedData) -> None:
    class Empty(FakeReader):
        async def freshness(self) -> Freshness:
            return Freshness(None, None)

    with pytest.raises(NoDataError):
        await dashboard_summary(Empty(data, ORG, NOW))
    with pytest.raises(NoDataError):
        await campaign_overview(Empty(data, ORG, NOW), LAST_28)


# ---- campaign health vs planted scenarios --------------------------------------------------
async def test_overview_is_mature_only_and_complete(
    reader: FakeReader, data: GeneratedData
) -> None:
    o = await campaign_overview(reader, LAST_28)
    assert o.truncated_days == 3
    assert o.period.end == END - timedelta(days=3)
    assert len(o.rows) == len(data.campaigns)
    assert o.provenance.as_of == NOW
    assert all(r.info.data_source.value == "MOCK" for r in o.rows)


async def test_high_acos_campaigns_are_unhealthy_and_nothing_else_is(
    reader: FakeReader, data: GeneratedData
) -> None:
    o = await campaign_overview(reader, LAST_28)
    assert ext(o.rows, HealthStatus.UNHEALTHY) == set(data.truth.high_acos_campaign_ids)


async def test_budget_constrained_flag_matches_the_planted_budget_capped_campaigns(
    reader: FakeReader, data: GeneratedData
) -> None:
    o = await campaign_overview(reader, LAST_28)
    flagged = {r.info.external_id for r in o.rows if r.budget.constrained}
    assert flagged == set(data.truth.budget_capped_campaign_ids)
    for r in o.rows:
        if r.info.external_id in flagged:
            assert r.budget.utilization_pct is not None
            assert r.budget.utilization_pct > 80


async def test_spend_distribution_is_consistent(reader: FakeReader) -> None:
    o = await campaign_overview(reader, LAST_28)
    d = o.distribution
    assert [s.share_pct for s in d] == sorted((s.share_pct for s in d), reverse=True)
    assert abs(sum((s.share_pct for s in d), D(0)) - 100) < D("1e-20")
    assert abs(d[-1].cumulative_pct - 100) < D("1e-20")
    by_id = {r.info.id: r for r in o.rows}
    total = sum(r.totals.spend_paise for r in o.rows)
    for s in d:
        assert s.share_pct == D(by_id[s.campaign_id].totals.spend_paise * 100) / D(total)


async def test_stricter_target_acos_never_makes_campaigns_healthier(reader: FakeReader) -> None:
    def unhealthy(o: CampaignOverview) -> int:
        return len(ext(o.rows, HealthStatus.UNHEALTHY))

    strict = unhealthy(await campaign_overview(reader, LAST_28, target_acos_pct=D(10)))
    normal = unhealthy(await campaign_overview(reader, LAST_28, target_acos_pct=D(30)))
    lax = unhealthy(await campaign_overview(reader, LAST_28, target_acos_pct=D(80)))
    assert strict >= normal >= lax
    assert strict > lax  # the target genuinely changes the verdicts
    with pytest.raises(ValueError, match="positive"):
        await campaign_overview(reader, LAST_28, target_acos_pct=D(0))


async def test_fully_immature_request_raises(reader: FakeReader) -> None:
    with pytest.raises(NoMatureDataError):
        await campaign_overview(reader, DateRange(END - timedelta(days=1), END))


# ---- "why did ACOS change" on the planted anomaly ------------------------------------------
async def test_anomaly_campaigns_acos_rise_is_attributed_mainly_to_conversion_rate(
    reader: FakeReader, data: GeneratedData
) -> None:
    changes = {
        c.info.external_id: c
        for c in await campaign_changes(reader, DateRange(END - timedelta(days=13), END))
    }
    assert data.truth.anomaly_campaign_ids
    for cid in data.truth.anomaly_campaign_ids:
        e = changes[cid].acos_explanation
        assert e is not None
        assert e.change_points > 0
        assert e.main_driver.name == "cvr"
        assert e.main_driver.contribution_points > D("0.6") * e.change_points
        assert changes[cid].current.end == END - timedelta(days=3)  # lag days excluded


# ---- pure rules ----------------------------------------------------------------------------
def t(clicks: int, spend: int, orders: int, sales: int) -> MetricTotals:
    return MetricTotals(clicks * 100, clicks, spend, sales, orders, orders)


def test_health_rules_and_boundaries() -> None:
    target = D(30)
    assert assess_health(t(MIN_CLICKS_FOR_HEALTH - 1, 1000, 5, 100_000), target).status is (
        HealthStatus.INSUFFICIENT_DATA
    )
    assert assess_health(t(500, 50_000, 0, 0), target).status is HealthStatus.UNHEALTHY
    assert assess_health(t(500, 30_000, 10, 100_000), target).status is HealthStatus.HEALTHY  # =30%
    assert assess_health(t(500, 39_000, 10, 100_000), target).status is HealthStatus.WATCH  # =39%
    assert assess_health(t(500, 39_001, 10, 100_000), target).status is HealthStatus.UNHEALTHY
    assert assess_health(t(500, 10_000, 10, 0), target).status is HealthStatus.INSUFFICIENT_DATA


def test_health_never_treats_missing_sales_as_perfect() -> None:
    h = assess_health(t(500, 10_000, 3, 0), D(30))
    assert h.status is not HealthStatus.HEALTHY


def test_budget_rules() -> None:
    capped = assess_budget([950, 1000, 990, 1000], 1000, 4)
    assert capped.constrained
    assert capped.days_at_cap_pct == D(100)
    assert capped.utilization_pct is not None
    assert D("97") < capped.utilization_pct < D("99")
    loose = assess_budget([100, 200, 150, 950], 1000, 4)
    assert not loose.constrained
    assert loose.days_at_cap_pct == D(25)
    assert assess_budget([], 1000, 4).utilization_pct is None
    assert assess_budget([500], 0, 4).utilization_pct is None
