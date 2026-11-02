from decimal import Decimal

from hypothesis import assume, given
from hypothesis import strategies as st

from app.domain.kpi.explain import explain_acos_change
from app.domain.kpi.metrics import MetricTotals

D = Decimal


def totals(clicks: int, spend: int, orders: int, sales: int) -> MetricTotals:
    return MetricTotals(clicks * 100, clicks, spend, sales, orders, orders)


BASE = totals(
    clicks=1000, spend=1_000_000, orders=100, sales=5_000_000
)  # CPC 1000, CVR 10%, AOV 50000


def test_cvr_halving_is_attributed_to_conversion_rate() -> None:
    # Same CPC and AOV, half the orders: ACOS doubles from 20% to 40%.
    worse = totals(clicks=1000, spend=1_000_000, orders=50, sales=2_500_000)
    e = explain_acos_change(worse, BASE)
    assert e is not None
    assert (e.previous_pct, e.current_pct, e.change_points) == (D(20), D(40), D(20))
    assert e.main_driver.name == "cvr"
    assert abs(e.main_driver.contribution_points - 20) < D("1e-20")
    assert all(abs(d.contribution_points) < D("1e-20") for d in e.drivers[1:])


def test_cpc_increase_is_attributed_to_cpc() -> None:
    pricier = totals(clicks=1000, spend=1_500_000, orders=100, sales=5_000_000)
    e = explain_acos_change(pricier, BASE)
    assert e is not None
    assert e.main_driver.name == "cpc"
    assert e.main_driver.log_effect > 0  # pushed ACOS up


def test_higher_order_value_improves_acos_and_is_attributed_to_aov() -> None:
    richer = totals(clicks=1000, spend=1_000_000, orders=100, sales=10_000_000)
    e = explain_acos_change(richer, BASE)
    assert e is not None
    assert e.change_points < 0
    assert e.main_driver.name == "aov"
    assert e.main_driver.log_effect < 0


def test_no_change_gives_zero_contributions() -> None:
    e = explain_acos_change(BASE, BASE)
    assert e is not None
    assert e.change_points == 0
    assert all(d.contribution_points == 0 for d in e.drivers)


def test_undefined_when_any_factor_is_missing() -> None:
    no_orders = totals(clicks=1000, spend=1_000_000, orders=0, sales=0)
    assert explain_acos_change(no_orders, BASE) is None
    assert explain_acos_change(BASE, no_orders) is None
    assert explain_acos_change(MetricTotals(), BASE) is None


def test_note_states_this_is_not_causal() -> None:
    e = explain_acos_change(BASE, BASE)
    assert e is not None
    assert "not why" in e.note


pos = st.integers(min_value=1, max_value=10**7)


@given(c1=pos, s1=pos, o1=pos, a1=pos, c0=pos, s0=pos, o0=pos, a0=pos)
def test_contributions_always_sum_to_the_acos_change(
    c1: int, s1: int, o1: int, a1: int, c0: int, s0: int, o0: int, a0: int
) -> None:
    assume(o1 <= c1 and o0 <= c0)
    e = explain_acos_change(totals(c1, s1, o1, a1), totals(c0, s0, o0, a0))
    assert e is not None
    assert abs(sum(d.contribution_points for d in e.drivers) - e.change_points) < D("1e-15")
    log_total = sum(d.log_effect for d in e.drivers)
    expected = (e.current_pct / e.previous_pct).ln()
    assert abs(log_total - expected) < D("1e-20")
