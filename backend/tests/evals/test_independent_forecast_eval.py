"""Forecast evaluation on an INDEPENDENT series generator, seeds 101-120.

PRE-REGISTERED before any run. Purpose: the mock generator's weekly pattern is too regular and it
planted the scenarios the detectors were tested on. This evaluation uses a differently-built source
(level drift and steps, evolving weekly shape, promotions and outliers that may fall in the
holdout, wandering ROAS) so results do not depend on the mock generator's structure.

Protocol: 20 seeds x {spend, sales} = 40 cases. Each series has 131 days; the last 14 are hidden.
Every model is fitted on the earlier 117 days and scored on the hidden 14. The opt-in selection
rule (``allow_learned=True``) and the shipped default (``allow_learned`` unset) each run through
``forecast_series`` on the same history. Metrics: MAE, RMSE, MAPE (skipping zeros), WAPE.

Criteria (the shipped default is seasonal-naive, ADR 0013 update):
  S1  shipped default mean WAPE <= 0.25
  S2  shipped default 80% interval coverage, pooled over cases and days, within 0.65-0.92
  S3  no negative bound; every interval contains its point forecast (all cases)
Recommendation criteria, on the SAME bars as the earlier failed forecast evaluations; they do not
change the default by themselves and any change needs an owner decision:
  R1  opt-in learned selection: holdout MAE <= seasonal-naive in >= 70% of cases AND mean
      relative MAE improvement >= 5%
  R2  seasonal_mean_4: the same two bars
A failed criterion is a recorded known failure, not a reason to change the criterion or the
generator.
Seeds 101-120 are used for nothing else; they are spent after this run.

Synthetic data: these results say nothing about real Amazon accounts.
"""

import statistics
from datetime import date, timedelta
from typing import Any

import numpy as np
import pytest

from app.application.forecasting.metrics import mae, mape, rmse, wape
from app.application.forecasting.models import default_forecasters
from app.application.forecasting.service import forecast_series
from tests.evals.independent_series import make_series

pytestmark = pytest.mark.eval

SEEDS = list(range(101, 121))
HORIZON = 14
END = date(2026, 10, 28)  # last day of the 131-day series; only its weekday matters
MAX_DEFAULT_WAPE = 0.25
COVERAGE_RANGE = (0.65, 0.92)
MIN_NOT_WORSE_SHARE = 0.70
MIN_MEAN_IMPROVEMENT = 0.05

Row = dict[str, Any]


def run_cases() -> list[Row]:
    rows: list[Row] = []
    models = {m.name: m for m in default_forecasters()}
    for seed in SEEDS:
        spend, sales = make_series(seed)
        for metric, values in (("spend", spend), ("sales", sales)):
            history, actual = values[:-HORIZON], values[-HORIZON:]
            hist_end = END - timedelta(days=HORIZON)
            row: Row = {"seed": seed, "metric": metric}
            for name, model in models.items():
                try:
                    f = np.asarray(
                        model.forecast(history, HORIZON, hist_end.weekday()), dtype=float
                    )
                except Exception:  # a model failing on a series is counted as worse
                    row[name] = None
                    continue
                row[name] = (mae(actual, f), rmse(actual, f), mape(actual, f), wape(actual, f))
            learned = forecast_series(history, hist_end, allow_learned=True)
            default = forecast_series(history, hist_end)
            lp = np.asarray(learned.point)
            row["optin"] = (mae(actual, lp), rmse(actual, lp), mape(actual, lp), wape(actual, lp))
            row["optin_model"] = learned.selected_model
            lo, hi, pt = (np.asarray(x) for x in (default.lower, default.upper, default.point))
            row["default_cov"] = int(((actual >= lo) & (actual <= hi)).sum())
            row["default_sane"] = bool((lo >= 0).all() and (pt >= lo).all() and (pt <= hi).all())
            row["default_wape"] = wape(actual, pt)
            rows.append(row)
    return rows


@pytest.fixture(scope="module")
def cases() -> list[Row]:
    return run_cases()


def _share_and_gain(cases: list[Row], key: str) -> tuple[float, float]:
    ok = [
        (c[key][0], c["seasonal_naive"][0])
        for c in cases
        if c.get(key) is not None and c["seasonal_naive"][0] > 0
    ]
    share = sum(a <= b for a, b in ok) / len(cases)  # a failed model counts as worse
    gain = statistics.fmean((b - a) / b for a, b in ok)
    return share, gain


def _coverage(cases: list[Row]) -> float:
    return float(sum(c["default_cov"] for c in cases) / (len(cases) * HORIZON))


def test_report(cases: list[Row]) -> None:
    """Run with ``pytest -s -m eval`` to print the numbers for the docs."""
    for key in [m.name for m in default_forecasters()] + ["optin"]:
        got = [c[key] for c in cases if c.get(key) is not None]
        share, gain = _share_and_gain(cases, key)
        mapes = [g[2] for g in got if g[2] is not None]
        wapes = [g[3] for g in got if g[3] is not None]
        print(
            f"{key:18s} n={len(got):2d} MAE={statistics.fmean(g[0] for g in got):12.0f} "
            f"RMSE={statistics.fmean(g[1] for g in got):12.0f} "
            f"MAPE={statistics.fmean(mapes):.3f} WAPE={statistics.fmean(wapes):.3f} "
            f"not_worse={share:.2f} gain={gain:+.3f}"
        )
    print("default WAPE", round(statistics.fmean(c["default_wape"] for c in cases), 3))
    print("default coverage", round(_coverage(cases), 3))
    print("opt-in models", [c["optin_model"] for c in cases])


def test_default_intervals_are_sane(cases: list[Row]) -> None:
    assert all(c["default_sane"] for c in cases)  # S3


def test_default_interval_coverage(cases: list[Row]) -> None:
    assert COVERAGE_RANGE[0] <= _coverage(cases) <= COVERAGE_RANGE[1]  # S2


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN, independent generator seeds 101-120: shipped default (seasonal-naive) mean WAPE "
        "0.294 > 0.25. It was the least accurate of the simple baselines here "
        "(seasonal_mean_4 0.222, moving_average_7 0.242)."
    ),
)
def test_default_is_reasonably_accurate(cases: list[Row]) -> None:
    assert statistics.fmean(c["default_wape"] for c in cases) <= MAX_DEFAULT_WAPE  # S1


def test_optin_learned_selection_beats_seasonal_naive(cases: list[Row]) -> None:
    share, gain = _share_and_gain(cases, "optin")  # R1
    assert share >= MIN_NOT_WORSE_SHARE and gain >= MIN_MEAN_IMPROVEMENT


def test_seasonal_mean_4_beats_seasonal_naive(cases: list[Row]) -> None:
    share, gain = _share_and_gain(cases, "seasonal_mean_4")  # R2
    assert share >= MIN_NOT_WORSE_SHARE and gain >= MIN_MEAN_IMPROVEMENT
