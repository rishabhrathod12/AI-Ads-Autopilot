import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.stats import (
    binomial_cdf,
    binomial_upper_tail,
    chance_of_at_most,
    clicks_needed_to_rule_out,
    exact_lower_bound,
)


def exact_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


@pytest.mark.parametrize(
    ("k", "n", "p"), [(0, 10, 0.1), (2, 20, 0.09), (5, 30, 0.09), (0, 31, 0.09), (3, 7, 0.5)]
)
def test_binomial_cdf_matches_exact_sum(k: int, n: int, p: float) -> None:
    assert binomial_cdf(k, n, p) == pytest.approx(exact_cdf(k, n, p), rel=1e-12)


def test_known_values() -> None:
    assert binomial_cdf(0, 10, 0.5) == pytest.approx(1 / 1024)
    assert binomial_cdf(10, 10, 0.3) == 1.0
    assert binomial_cdf(0, 5, 0.0) == 1.0
    assert binomial_cdf(0, 5, 1.0) == 0.0
    # zero orders from 30 clicks at a 9% conversion rate: 0.91^30 is about 5.9%
    assert chance_of_at_most(0, 30, 0.09) == pytest.approx(0.91**30)


def test_large_n_does_not_underflow_or_overflow() -> None:
    value = binomial_cdf(5, 100_000, 0.09)
    assert 0.0 <= value < 1e-100  # astronomically unlikely, and no exception
    assert 0.0 <= binomial_cdf(50_000, 100_000, 0.5) <= 1.0
    assert binomial_cdf(50_000, 100_000, 0.5) == pytest.approx(0.5, abs=0.01)


def test_binomial_rejects_invalid_arguments() -> None:
    for args in [(-1, 5, 0.5), (1, -5, 0.5), (1, 5, 1.5), (1, 5, -0.1)]:
        with pytest.raises(ValueError):
            binomial_cdf(*args)


@given(n=st.integers(1, 300), p=st.floats(0.001, 0.999), data=st.data())
def test_cdf_is_monotone_in_k_and_bounded(n: int, p: float, data: st.DataObject) -> None:
    k = data.draw(st.integers(0, n))
    here = binomial_cdf(k, n, p)
    assert 0.0 <= here <= 1.0
    assert binomial_cdf(min(k + 1, n), n, p) >= here - 1e-12


@given(n=st.integers(1, 200), k=st.integers(0, 200), p=st.floats(0.001, 0.5))
def test_more_clicks_make_a_given_shortfall_less_likely(n: int, k: int, p: float) -> None:
    k = min(k, n)
    assert chance_of_at_most(k, n + 50, p) <= chance_of_at_most(k, n, p) + 1e-12


def test_exact_lower_bound_ranks_evidence_not_luck() -> None:
    assert exact_lower_bound(1, 1) < exact_lower_bound(20, 100)  # 100% of 1 vs 20% of 100
    assert exact_lower_bound(2, 2) < exact_lower_bound(20, 20)  # perfect, but thin evidence
    assert exact_lower_bound(5, 20) < exact_lower_bound(10, 20)
    assert exact_lower_bound(3, 10) < exact_lower_bound(30, 100)  # same rate, more data
    assert exact_lower_bound(0, 50) == 0.0
    assert exact_lower_bound(0, 0) == 0.0
    assert exact_lower_bound(1, 1) == pytest.approx(0.05)
    assert exact_lower_bound(4, 4) == pytest.approx(0.05**0.25)
    with pytest.raises(ValueError):
        exact_lower_bound(5, 3)
    with pytest.raises(ValueError):
        exact_lower_bound(1, 5, alpha=0.0)


@pytest.mark.parametrize(("k", "n"), [(1, 20), (5, 20), (20, 100), (7, 300), (150, 1000)])
def test_exact_lower_bound_satisfies_its_defining_property(k: int, n: int) -> None:
    lb = exact_lower_bound(k, n)
    upper_tail = binomial_upper_tail(k, n, lb)
    if n <= 300:
        assert upper_tail == pytest.approx(1.0 - exact_cdf(k - 1, n, lb), abs=1e-12)
    assert upper_tail == pytest.approx(0.05, abs=1e-6)  # P(X >= k | lb) = alpha
    assert lb < k / n


@given(n=st.integers(1, 2_000), data=st.data())
def test_exact_lower_bound_is_bounded_by_the_observed_rate(n: int, data: st.DataObject) -> None:
    k = data.draw(st.integers(0, n))
    assert 0.0 <= exact_lower_bound(k, n) <= k / n + 1e-12


def test_clicks_needed_to_rule_out() -> None:
    assert clicks_needed_to_rule_out(0.09, 0.05) == 32  # 0.91^31 = 5.4%, 0.91^32 = 4.9%
    assert clicks_needed_to_rule_out(0.0, 0.05) is None
    assert clicks_needed_to_rule_out(1.0, 0.05) == 1
    n = clicks_needed_to_rule_out(0.07, 0.05)
    assert n is not None
    assert chance_of_at_most(0, n, 0.07) <= 0.05 < chance_of_at_most(0, n - 1, 0.07)
    with pytest.raises(ValueError):
        clicks_needed_to_rule_out(0.1, 0.0)


@pytest.mark.parametrize(
    ("k", "n", "p"), [(1, 10, 0.2), (9, 10, 0.2), (150, 300, 0.5), (3, 8, 0.4)]
)
def test_upper_tail_is_the_complement_of_the_cdf(k: int, n: int, p: float) -> None:
    assert binomial_upper_tail(k, n, p) == pytest.approx(1.0 - exact_cdf(k - 1, n, p), abs=1e-12)
    assert binomial_upper_tail(0, n, p) == 1.0
    assert binomial_upper_tail(n + 1, n, p) == 0.0
