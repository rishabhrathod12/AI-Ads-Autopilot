from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.kpi.metrics import (
    KPI_NAMES,
    KpiChange,
    Kpis,
    MetricTotals,
    acos_pct,
    aov_paise,
    compare_totals,
    cpc_paise,
    ctr_pct,
    cvr_pct,
    roas,
    round_dp,
)

D = Decimal


# ---- golden cases, calculated by hand ------------------------------------------------------
def test_golden_kpis() -> None:
    # Rs 1,000 spend, Rs 5,000 attributed sales, 20,000 impressions, 200 clicks, 10 orders
    t = MetricTotals(
        impressions=20_000,
        clicks=200,
        spend_paise=100_000,
        sales_paise=500_000,
        orders=10,
        units=12,
    )
    k = Kpis.from_totals(t)
    assert k.acos_pct == D(20)  # 1000 / 5000
    assert k.roas == D(5)
    assert k.ctr_pct == D(1)  # 200 / 20000
    assert k.cpc_paise == D(500)  # Rs 5.00
    assert k.cvr_pct == D(5)  # 10 / 200
    assert k.aov_paise == D(50_000)  # Rs 500


def test_results_are_exact_decimals_not_floats() -> None:
    assert acos_pct(1, 3) == D("33.33333333333333333333333333")  # 28 significant digits
    assert isinstance(cvr_pct(1, 3), Decimal)
    assert cvr_pct(3, 10) == D(30)  # float math would give 30.000000000000004


@pytest.mark.parametrize(
    ("fn", "args"),
    [
        (acos_pct, (100, 0)),
        (roas, (100, 0)),
        (ctr_pct, (5, 0)),
        (cpc_paise, (100, 0)),
        (cvr_pct, (5, 0)),
        (aov_paise, (100, 0)),
    ],
)
def test_zero_denominator_is_undefined_not_zero(fn: object, args: tuple[int, int]) -> None:
    assert fn(*args) is None  # type: ignore[operator]


def test_zero_numerator_is_a_real_zero() -> None:
    # Spend with no sales: ACOS is undefined, ROAS is a genuine 0.
    assert acos_pct(100, 0) is None
    assert roas(0, 100) == D(0)
    assert cvr_pct(0, 50) == D(0)


def test_empty_totals_give_all_undefined() -> None:
    k = Kpis.from_totals(MetricTotals())
    assert all(getattr(k, n) is None for n in KPI_NAMES)


def test_aggregate_then_ratio_not_average_of_ratios() -> None:
    a = MetricTotals(spend_paise=100, sales_paise=1_000)  # ACOS 10%
    b = MetricTotals(spend_paise=500, sales_paise=500)  # ACOS 100%
    naive_mean = (acos_pct(100, 1_000) + acos_pct(500, 500)) / 2  # type: ignore[operator]
    true_acos = Kpis.from_totals(a + b).acos_pct
    assert naive_mean == D(55)
    assert true_acos == D(40)  # 600 / 1500: the only correct answer


def test_totals_reject_negative_or_non_integer_values() -> None:
    for bad in (-1, 1.5, True):
        with pytest.raises(ValueError, match="non-negative integer"):
            MetricTotals(clicks=bad)  # type: ignore[arg-type]


def test_round_dp_is_half_up_and_none_safe() -> None:
    assert round_dp(D("2.345"), 2) == D("2.35")
    assert round_dp(D("2.344"), 2) == D("2.34")
    assert round_dp(D("2.5"), 0) == D(3)
    assert round_dp(None) is None


# ---- comparison ----------------------------------------------------------------------------
def test_kpi_change_values() -> None:
    c = KpiChange(current=D(30), previous=D(20))
    assert c.absolute == D(10)  # percentage points for % metrics
    assert c.relative_pct == D(50)
    assert KpiChange(D(10), D(20)).relative_pct == D(-50)


@pytest.mark.parametrize(
    "change",
    [KpiChange(None, D(5)), KpiChange(D(5), None), KpiChange(None, None)],
)
def test_change_with_undefined_side_is_undefined(change: KpiChange) -> None:
    assert change.absolute is None
    assert change.relative_pct is None


def test_relative_change_from_zero_is_undefined() -> None:
    c = KpiChange(D(5), D(0))
    assert c.absolute == D(5)
    assert c.relative_pct is None  # cannot express growth from zero as a percentage


def test_compare_totals_covers_every_metric() -> None:
    cur = MetricTotals(1000, 20, 20_000, 100_000, 4, 4)
    prev = MetricTotals(1000, 10, 10_000, 100_000, 5, 5)
    changes = compare_totals(cur, prev)
    assert set(changes) == {
        *KPI_NAMES,
        "impressions",
        "clicks",
        "spend_paise",
        "sales_paise",
        "orders",
        "units",
    }
    assert changes["acos_pct"].absolute == D(10)  # 20% vs 10%
    assert changes["spend_paise"].relative_pct == D(100)
    assert changes["cvr_pct"].current == D(20)  # 4/20


# ---- properties ----------------------------------------------------------------------------
counts = st.integers(min_value=0, max_value=10**9)
money = st.integers(min_value=0, max_value=10**12)
totals_st = st.builds(MetricTotals, counts, counts, money, money, counts, counts)
positive_money = st.integers(min_value=1, max_value=10**12)


@given(spend=positive_money, sales=positive_money)
def test_acos_and_roas_are_reciprocal(spend: int, sales: int) -> None:
    a, r = acos_pct(spend, sales), roas(sales, spend)
    assert a is not None
    assert r is not None
    assert abs(a * r - 100) < D("1e-18")


@given(totals_st)
def test_kpi_is_undefined_exactly_when_denominator_is_zero(t: MetricTotals) -> None:
    k = Kpis.from_totals(t)
    assert (k.acos_pct is None) == (t.sales_paise == 0)
    assert (k.roas is None) == (t.spend_paise == 0)
    assert (k.ctr_pct is None) == (t.impressions == 0)
    assert (k.cpc_paise is None) == (t.clicks == 0)
    assert (k.cvr_pct is None) == (t.clicks == 0)
    assert (k.aov_paise is None) == (t.orders == 0)


@given(totals_st)
def test_defined_kpis_are_never_negative(t: MetricTotals) -> None:
    for name in KPI_NAMES:
        value = getattr(Kpis.from_totals(t), name)
        assert value is None or value >= 0


@given(a=totals_st, b=totals_st, c=totals_st)
def test_totals_form_a_commutative_monoid(
    a: MetricTotals, b: MetricTotals, c: MetricTotals
) -> None:
    assert a + b == b + a
    assert (a + b) + c == a + (b + c)
    assert a + MetricTotals() == a
    assert MetricTotals.sum([a, b, c]) == a + b + c


@given(st.integers(0, 10**6), st.integers(0, 10**6), st.integers(1, 10**6))
def test_percentages_stay_within_bounds_for_valid_funnels(
    c: int, extra: int, orders_cap: int
) -> None:
    clicks = c
    impressions = clicks + extra
    orders = min(orders_cap, clicks)
    ctr, cvr = ctr_pct(clicks, impressions), cvr_pct(orders, clicks)
    assert ctr is None or ctr <= 100
    assert cvr is None or cvr <= 100


@given(a=totals_st, b=totals_st)
def test_kpis_of_a_union_come_from_summed_counts(a: MetricTotals, b: MetricTotals) -> None:
    """Union KPIs are computed from summed counts, so they match a direct computation."""
    union = Kpis.from_totals(a + b)
    clicks, spend = a.clicks + b.clicks, a.spend_paise + b.spend_paise
    if clicks == 0:
        assert union.cpc_paise is None
    else:
        assert union.cpc_paise == cpc_paise(spend, clicks)
        assert union.cpc_paise is not None
        assert abs(union.cpc_paise - D(spend) / D(clicks)) < D("1e-15")
