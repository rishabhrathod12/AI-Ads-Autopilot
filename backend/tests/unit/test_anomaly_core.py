import math
from collections.abc import Sequence

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.application.analytics.thresholds import CVR_H_ALERT, CVR_H_WATCH, CVR_RATIO_DOWN
from app.domain.anomaly import (
    Direction,
    Trace,
    bernoulli_trace,
    find_runs,
    gaussian_trace,
    robust_center_scale,
)

P0 = 0.09


def steady(clicks: int = 60) -> tuple[int, int]:
    return round(clicks * P0), clicks


# ---- Bernoulli CUSUM -----------------------------------------------------------------------
def test_single_day_evidence_matches_the_formula() -> None:
    t = bernoulli_trace([0], [100], 0.10, Direction.DOWN, 0.6)
    assert t.increments[0] == pytest.approx(100 * math.log(0.94 / 0.90))  # 100 misses at p1=0.06
    t = bernoulli_trace([10], [100], 0.10, Direction.DOWN, 0.6)
    assert t.increments[0] == pytest.approx(10 * math.log(0.6) + 90 * math.log(0.94 / 0.90))


def test_steady_rate_never_alarms() -> None:
    orders, clicks = zip(*[steady() for _ in range(14)], strict=True)
    for direction, ratio in ((Direction.DOWN, 0.6), (Direction.UP, 1.5)):
        trace = bernoulli_trace(orders, clicks, P0, direction, ratio)
        assert find_runs(trace, CVR_H_WATCH, CVR_H_ALERT) == []


def test_a_sustained_halving_is_detected_and_dated() -> None:
    normal, halved = steady(60), (round(60 * P0 / 2), 60)
    orders, clicks = zip(*([normal] * 3 + [halved] * 11), strict=True)
    down = bernoulli_trace(orders, clicks, P0, Direction.DOWN, CVR_RATIO_DOWN)
    runs = find_runs(down, CVR_H_WATCH, CVR_H_ALERT)
    assert len(runs) == 1
    run = runs[0]
    assert run.ongoing
    assert run.first_alert is not None
    assert 2 <= run.start <= 4  # onset estimated close to the true day 3
    assert run.first_watch <= run.first_alert
    assert run.first_watch - 3 <= 7  # watch-level within about a week of the change
    up = bernoulli_trace(orders, clicks, P0, Direction.UP, 1.5)
    assert find_runs(up, CVR_H_WATCH, CVR_H_ALERT) == []


def test_days_with_no_clicks_add_no_evidence() -> None:
    t = bernoulli_trace([0, 0, 0], [0, 0, 0], P0, Direction.DOWN, 0.6)
    assert t.path == [0.0, 0.0, 0.0]


def test_a_small_sample_cannot_alarm_alone() -> None:
    t = bernoulli_trace([0], [5], P0, Direction.DOWN, 0.6)  # 5 clicks, no orders: unremarkable
    assert find_runs(t, CVR_H_WATCH, CVR_H_ALERT) == []


@pytest.mark.parametrize(
    ("p0", "direction", "ratio"),
    [(0.0, Direction.DOWN, 0.6), (1.0, Direction.DOWN, 0.6), (0.1, Direction.DOWN, 1.2),
     (0.1, Direction.UP, 0.8)],
)  # fmt: skip
def test_invalid_design_is_rejected(p0: float, direction: Direction, ratio: float) -> None:
    with pytest.raises(ValueError):
        bernoulli_trace([1], [10], p0, direction, ratio)


def test_orders_cannot_exceed_clicks() -> None:
    with pytest.raises(ValueError, match="orders <= clicks"):
        bernoulli_trace([5], [3], 0.1, Direction.DOWN, 0.6)


# ---- Gaussian CUSUM ------------------------------------------------------------------------
def test_gaussian_accumulates_only_beyond_the_slack() -> None:
    up = gaussian_trace([2.0, 2.0, 2.0], Direction.UP, k=0.5)
    assert up.path == [1.5, 3.0, 4.5]
    assert gaussian_trace([2.0, 2.0], Direction.DOWN).path == [0.0, 0.0]
    assert gaussian_trace([0.4, -0.4, 0.3], Direction.UP).path == [
        0.0,
        0.0,
        0.0,
    ]  # inside the slack


def test_missing_days_neither_add_nor_remove_evidence() -> None:
    t = gaussian_trace([2.0, None, None, 2.0], Direction.UP)
    assert t.path == [1.5, 1.5, 1.5, 3.0]


# ---- run extraction ------------------------------------------------------------------------
def test_spike_then_recovery_is_reported_as_subsided() -> None:
    z: Sequence[float | None] = [0.0] * 4 + [6.0, 6.0] + [0.0] * 8
    runs = find_runs(gaussian_trace(z, Direction.UP), 7.0, 10.5)
    assert len(runs) == 1
    assert runs[0].first_watch == 5
    assert runs[0].first_alert == 5  # 5.5 + 5.5 = 11
    assert not runs[0].ongoing  # the last days only subtract


def test_sustained_shift_is_ongoing() -> None:
    z: Sequence[float | None] = [0.0] * 6 + [2.0] * 8
    runs = find_runs(gaussian_trace(z, Direction.UP), 4.0, 6.0)
    assert len(runs) == 1
    assert runs[0].ongoing
    assert runs[0].start == 6


def test_separate_episodes_are_separate_runs() -> None:
    path = [0.0, 5.0, 5.0, 0.0, 0.0, 5.0, 5.0, 5.0]
    runs = find_runs(Trace(path, [0.0] * len(path)), 4.0, 8.0)
    assert [(r.start, r.end) for r in runs] == [(1, 2), (5, 7)]


def test_runs_below_the_watch_bar_are_ignored() -> None:
    assert find_runs(Trace([0.0, 3.9, 3.9, 0.0], [0.0] * 4), 4.0, 6.0) == []
    with pytest.raises(ValueError, match="h_alert"):
        find_runs(Trace([0.0], [0.0]), 6.0, 4.0)


def test_robust_scale_ignores_outliers_and_rejects_flat_data() -> None:
    centre, sigma = robust_center_scale([10, 11, 9, 10, 10, 11, 9, 1000])
    assert centre == pytest.approx(10.0)
    assert sigma < 3
    with pytest.raises(ValueError):
        robust_center_scale([5, 5, 5, 5])
    with pytest.raises(ValueError):
        robust_center_scale([5])


@given(st.lists(st.floats(-6, 6), min_size=1, max_size=60))
def test_statistic_is_never_negative_and_follows_the_recursion(values: list[float]) -> None:
    t = gaussian_trace(values, Direction.UP)
    previous = 0.0
    for s, inc in zip(t.path, t.increments, strict=True):
        assert s >= 0.0
        assert s == pytest.approx(max(0.0, previous + inc))
        previous = s


# ---- the calibration claim, re-checked on a smaller simulation -----------------------------
def test_false_alarm_rate_under_no_change_matches_the_calibration() -> None:
    rng = np.random.default_rng(7)
    reps, watch_hits, alert_hits = 1500, 0, 0
    for _ in range(reps):
        ref_clicks = int(rng.poisson(58, 56).sum())
        p0 = rng.binomial(ref_clicks, P0) / ref_clicks
        clicks = rng.poisson(58, 14)
        orders = rng.binomial(clicks, P0)
        runs = []
        for direction, ratio in ((Direction.DOWN, 0.6), (Direction.UP, 1.5)):
            runs += find_runs(
                bernoulli_trace(orders.tolist(), clicks.tolist(), p0, direction, ratio),
                CVR_H_WATCH,
                CVR_H_ALERT,
            )
        watch_hits += bool(runs)
        alert_hits += any(r.first_alert is not None for r in runs)
    # calibrated for <= 3% / 0.5% per direction, so up to ~6% / 1% for either direction
    assert watch_hits / reps <= 0.075
    assert alert_hits / reps <= 0.02
