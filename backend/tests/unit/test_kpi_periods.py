from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.ads.models import DailyMetrics, DataSource, EntityType
from app.domain.kpi.metrics import Kpis, MetricTotals
from app.domain.kpi.periods import (
    DateRange,
    NoMatureDataError,
    aggregate_period,
    compare_periods,
    daily_totals,
    infer_mature_through,
)
from app.infrastructure.providers.mock.generator import MockConfig
from app.infrastructure.providers.mock.provider import MockAdvertisingDataProvider

D = Decimal
NOW = datetime(2026, 11, 1, tzinfo=UTC)
START = date(2026, 10, 1)


def row(
    day: int,
    *,
    entity: str = "c1",
    etype: EntityType = EntityType.CAMPAIGN,
    clicks: int = 10,
    spend: int = 10_000,
    orders: int = 2,
    sales: int = 50_000,
    mature: bool = True,
) -> DailyMetrics:
    return DailyMetrics(
        data_source=DataSource.MOCK,
        ingested_at=NOW,
        entity_type=etype,
        entity_id=entity,
        date=START + timedelta(days=day),
        impressions=clicks * 100,
        clicks=clicks,
        spend_paise=spend,
        sales_paise=sales if orders else 0,
        orders=orders,
        units=orders,
        attribution_days=7,
        is_mature=mature,
    )


# ---- DateRange -----------------------------------------------------------------------------
def test_date_range_basics() -> None:
    r = DateRange(date(2026, 10, 8), date(2026, 10, 14))
    assert r.days == 7
    assert r.previous() == DateRange(date(2026, 10, 1), date(2026, 10, 7))
    assert r.contains(date(2026, 10, 14))
    assert not r.contains(date(2026, 10, 15))
    assert DateRange(date(2026, 10, 1), date(2026, 10, 1)).days == 1


def test_date_range_rejects_inverted_range() -> None:
    with pytest.raises(ValueError, match="before start"):
        DateRange(date(2026, 10, 2), date(2026, 10, 1))


@given(
    start=st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 1, 1)),
    length=st.integers(0, 400),
)
def test_previous_period_is_adjacent_and_equal_length(start: date, length: int) -> None:
    r = DateRange(start, start + timedelta(days=length))
    p = r.previous()
    assert p.days == r.days
    assert p.end + timedelta(days=1) == r.start


# ---- aggregation ---------------------------------------------------------------------------
def test_aggregate_sums_then_computes_ratios() -> None:
    rows = [row(0, clicks=10, spend=10_000, orders=1, sales=100_000)]
    rows += [row(1, clicks=90, spend=90_000, orders=9, sales=100_000)]
    agg = aggregate_period(rows, DateRange(START, START + timedelta(days=1)))
    assert agg.totals.clicks == 100
    assert agg.active_days == 2
    # day ACOS: 10% and 90%; the true period ACOS is 100000 / 200000 = 50%, not (10 + 90) / 2
    assert agg.kpis.acos_pct == D(50)
    assert agg.kpis.cvr_pct == D(10)


def test_aggregate_respects_range_and_combines_entities_of_one_level() -> None:
    rows = [row(d, entity=e) for d in range(5) for e in ("c1", "c2")]
    agg = aggregate_period(rows, DateRange(START + timedelta(days=1), START + timedelta(days=2)))
    assert agg.active_days == 2
    assert agg.totals.clicks == 2 * 2 * 10


def test_aggregate_refuses_to_mix_hierarchy_levels() -> None:
    rows = [row(0), row(0, entity="g1", etype=EntityType.AD_GROUP)]
    with pytest.raises(ValueError, match="mix entity types"):
        aggregate_period(rows, DateRange(START, START))
    with pytest.raises(ValueError, match="mix entity types"):
        daily_totals(rows)


def test_immature_rows_can_be_excluded_and_are_counted() -> None:
    rows = [row(0), row(1), row(2, mature=False)]
    full = DateRange(START, START + timedelta(days=2))
    with_all = aggregate_period(rows, full, include_immature=True)
    mature_only = aggregate_period(rows, full, include_immature=False)
    assert (with_all.active_days, with_all.immature_days_included) == (3, 1)
    assert (mature_only.active_days, mature_only.immature_days_included) == (2, 0)
    assert mature_only.totals.clicks == 20


def test_empty_period_is_all_undefined_not_an_error() -> None:
    agg = aggregate_period(
        [row(0)], DateRange(START + timedelta(days=30), START + timedelta(days=31))
    )
    assert agg.totals == MetricTotals()
    assert agg.active_days == 0
    assert agg.kpis.acos_pct is None


def test_daily_totals_are_ordered_and_summed() -> None:
    rows = [row(2), row(0), row(0, entity="c2")]
    series = daily_totals(rows)
    assert list(series) == [START, START + timedelta(days=2)]
    assert series[START].clicks == 20


def test_infer_mature_through() -> None:
    assert infer_mature_through([row(0), row(1)]) is None
    assert infer_mature_through([row(0), row(1), row(2, mature=False)]) == START + timedelta(days=1)


# ---- comparison ----------------------------------------------------------------------------
def test_compare_with_previous_period() -> None:
    rows = [row(d, spend=10_000) for d in range(7)] + [row(d, spend=20_000) for d in range(7, 14)]
    cmp = compare_periods(rows, DateRange(START + timedelta(days=7), START + timedelta(days=13)))
    assert cmp.truncated_days == 0
    assert cmp.previous.range == DateRange(START, START + timedelta(days=6))
    assert cmp.changes["spend_paise"].relative_pct == D(100)
    assert cmp.changes["acos_pct"].current == D(40)  # 20000 * 7 / 350000
    assert cmp.changes["acos_pct"].absolute == D(20)  # percentage points
    assert cmp.changes["ctr_pct"].absolute == D(0)


def test_comparison_excludes_immature_days_and_realigns_the_previous_period() -> None:
    rows = [row(d) for d in range(11)] + [row(d, mature=False, orders=0) for d in range(11, 14)]
    requested = DateRange(START + timedelta(days=7), START + timedelta(days=13))
    cmp = compare_periods(rows, requested)
    assert cmp.truncated_days == 3
    assert cmp.current.range == DateRange(START + timedelta(days=7), START + timedelta(days=10))
    assert cmp.previous.range == DateRange(START + timedelta(days=3), START + timedelta(days=6))
    assert cmp.current.immature_days_included == 0
    # unbiased: both sides are clean 4-day mature windows with identical days, so no change
    assert cmp.changes["acos_pct"].absolute == D(0)


def test_comparison_can_include_immature_days_explicitly() -> None:
    rows = [row(d) for d in range(7)] + [row(d, mature=False, orders=0) for d in range(7, 10)]
    cmp = compare_periods(
        rows, DateRange(START + timedelta(days=7), START + timedelta(days=9)), include_immature=True
    )
    assert cmp.truncated_days == 0
    assert cmp.current.immature_days_included == 3
    assert cmp.changes["acos_pct"].current is None  # spend but no (yet attributed) sales


def test_comparison_of_a_fully_immature_period_raises() -> None:
    rows = [row(0), row(1, mature=False)]
    with pytest.raises(NoMatureDataError):
        compare_periods(rows, DateRange(START + timedelta(days=1), START + timedelta(days=1)))


# ---- cross-check against the generator's independent numpy sums ----------------------------
END = date(2026, 10, 31)
CFG = MockConfig(
    days=40,
    end_date=END,
    n_campaigns=6,
    ad_groups_per_campaign=2,
    keywords_per_ad_group=8,
    now=NOW,
)


async def collect(p: MockAdvertisingDataProvider, etype: EntityType) -> list[DailyMetrics]:
    rows: list[DailyMetrics] = []
    async for chunk in p.fetch_daily_metrics("MOCK-PRF-001", etype, END - timedelta(days=39), END):
        rows.extend(chunk)
    return rows


async def test_kpis_match_independent_numpy_totals_at_every_level() -> None:
    p = MockAdvertisingDataProvider(CFG)
    full = DateRange(END - timedelta(days=39), END)
    kpis_by_level: dict[str, Kpis] = {}
    for etype in EntityType:
        table = p.data.metrics[etype.value]
        agg = aggregate_period(await collect(p, etype), full)
        assert agg.totals.clicks == int(table.clicks.sum())
        assert agg.totals.spend_paise == int(table.spend_paise.sum())
        assert agg.totals.sales_paise == int(table.sales_paise.sum())
        assert agg.totals.orders == int(table.orders.sum())
        expected_acos = D(int(table.spend_paise.sum()) * 100) / D(int(table.sales_paise.sum()))
        assert agg.kpis.acos_pct is not None
        assert abs(agg.kpis.acos_pct - expected_acos) < D("1e-20")
        kpis_by_level[etype.value] = agg.kpis
    assert len(set(kpis_by_level.values())) == 1  # every level yields identical KPIs


async def test_daily_series_matches_numpy_and_flags_recent_days() -> None:
    p = MockAdvertisingDataProvider(CFG)
    rows = await collect(p, EntityType.CAMPAIGN)
    series = daily_totals(rows)
    table = p.data.metrics["campaign"]
    for day_idx, day in enumerate(p.data.dates):
        assert series[day].spend_paise == int(table.spend_paise[table.day == day_idx].sum())
    assert infer_mature_through(rows) == END - timedelta(days=3)
    cmp = compare_periods(rows, DateRange(END - timedelta(days=13), END))
    assert cmp.truncated_days == 3
    last_mature = END - timedelta(days=3)
    assert cmp.current.range.end == last_mature
    assert cmp.current.totals.clicks == sum(
        series[d].clicks for d in series if d >= END - timedelta(days=13) and d <= last_mature
    )
