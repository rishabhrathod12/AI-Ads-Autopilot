"""Should the default forecast be seasonal_mean_4 instead of seasonal-naive? Pre-registered, round 2.

PRE-REGISTERED before any run, on the owner's decision (2026-11-15) to re-run the SAME rule on fresh
seeds instead of deciding by hand. Round 1 (``test_seasonal_mean_comparison.py``, ML_EVALUATION.md 4e)
passed rules A and C and failed rule B: on 30 mock account cases seasonal_mean_4 was no worse in 43%.
With 30 cases a share has a standard error near 9 points, so round 1 could not separate "not
reliably better" from "worse". Round 2 doubles the account cases.

Rules A, B and C and their bars are IDENTICAL to round 1 and are not adjusted. Seeds are fresh: none
of them was used by any earlier evaluation (spent: mock 1-15, 21-60; independent 101-140; keyword
economic truth 41-45).

Sources and seeds:
  independent generator (``independent_series.py``), seeds 141-160: 20 seeds x {spend, sales} = 40 cases
  mock generator, seeds 61-90, mature days only, same hold-out protocol (last 14 days hidden):
    account level: 30 seeds x {spend, sales} = 60 cases
    campaign level: 30 seeds x 12 campaigns x {spend, sales} = up to 720 cases

Recommendation rule. The default is changed to seasonal_mean_4 only if ALL hold:
  A  independent: seasonal_mean_4 MAE <= seasonal-naive MAE in >= 70% of cases AND mean relative MAE
     improvement >= 5%
  B  mock account level (60 cases): the same two bars
  C  mock campaign level: MAE <= seasonal-naive in >= 60% of cases AND mean relative improvement >= 0
  D  interval calibration of the candidate, through the real code path
     (``forecast_series(..., default_model="seasonal_mean_4")``, intervals from its own out-of-sample
     backtest errors): pooled 80% interval coverage on the held-out 14 days within 0.65-0.92 for the
     independent account series (40) AND for the mock account series (60), no negative bound, and
     lower <= point <= upper everywhere. (Same bars as the shipped default had to meet, ML_EVALUATION.md 4d S2/S3.)
Otherwise the default stays seasonal-naive. Any mixed outcome is reported as it is; no bar is changed
after the run, and a failed rule is recorded as a strict known failure. If everything passes, the
default is changed in ``DEFAULT_MODEL`` (``forecasting/service.py``) in a separate, documented step.
Metrics reported: MAE, RMSE, MAPE, WAPE for both methods; interval coverage for both methods (not
gated for seasonal-naive). These results concern synthetic data only, not real accounts.
"""

import asyncio
from datetime import date, timedelta
from typing import NamedTuple

import numpy as np
import pytest

from app.application.forecasting.models import SeasonalMean4, SeasonalNaive
from app.application.forecasting.service import forecast_series
from app.domain.ads.models import EntityType
from app.domain.kpi.periods import DateRange
from app.infrastructure.providers.mock.generator import MockConfig, generate
from tests.evals.independent_series import make_series
from tests.evals.test_forecast_eval import END, HORIZON, NOW, ORG
from tests.evals.test_seasonal_mean_comparison import (
    GAIN_ACCOUNT,
    GAIN_CAMPAIGN,
    SHARE_ACCOUNT,
    SHARE_CAMPAIGN,
    Case,
    compare,
    describe,
    share_and_gain,
)
from tests.unit.fake_reader import FakeReader

pytestmark = pytest.mark.eval

INDEPENDENT_SEEDS = list(range(141, 161))
MOCK_SEEDS = list(range(61, 91))
COVERAGE_LOW, COVERAGE_HIGH = 0.65, 0.92


class Series(NamedTuple):
    values: np.ndarray  # full series incl. the 14 hidden days
    weekday: int  # weekday of the last day of the training history
    train_end: date  # date of the last day of the training history


class Collected(NamedTuple):
    independent: list[Series]
    mock_account: list[Series]
    mock_campaign: list[Series]


async def collect() -> Collected:
    independent: list[Series] = []
    for seed in INDEPENDENT_SEEDS:
        for values in make_series(seed):
            train_end = END - timedelta(days=HORIZON)
            independent.append(Series(values, train_end.weekday(), train_end))
    account: list[Series] = []
    campaign: list[Series] = []
    for seed in MOCK_SEEDS:
        data = generate(MockConfig(seed=seed, end_date=END, now=NOW))
        reader = FakeReader(data, ORG, NOW)
        end = END - timedelta(days=3)
        period = DateRange(data.dates[0], end)
        days = [period.start + timedelta(days=i) for i in range(period.days)]
        train_end = end - timedelta(days=HORIZON)

        def to_array(points: list, field: str, days: list = days) -> np.ndarray:  # type: ignore[type-arg]
            by_day = {p.day: getattr(p.totals, field) for p in points}
            return np.array([by_day.get(d, 0) for d in days], dtype=float)

        total = await reader.daily_totals(EntityType.CAMPAIGN, period, include_immature=False)
        for field in ("spend_paise", "sales_paise"):
            account.append(Series(to_array(total, field), train_end.weekday(), train_end))
        camps = await reader.campaigns()
        series = await reader.daily_by_entity(
            EntityType.CAMPAIGN, period, [x.id for x in camps], include_immature=False
        )
        for camp in camps:
            mine = [p for p in series if p.entity_id == camp.id]
            for field in ("spend_paise", "sales_paise"):
                campaign.append(Series(to_array(mine, field), train_end.weekday(), train_end))
    return Collected(independent, account, campaign)


def cases_of(series: list[Series]) -> list[Case]:
    out = []
    for s in series:
        c = compare(s.values, s.weekday)
        if c:
            out.append(c)
    return out


def coverage(series: list[Series], default_model: str) -> tuple[float, bool]:
    """(pooled 80% interval coverage on the hidden days, whether every interval was sane)."""
    inside = total = 0
    sane = True
    for s in series:
        if s.values.sum() == 0:
            continue
        history, actual = s.values[:-HORIZON], s.values[-HORIZON:]
        f = forecast_series(
            history,
            s.train_end,
            forecasters=[SeasonalNaive(), SeasonalMean4()],
            default_model=default_model,
        )
        point, lo, hi = np.array(f.point), np.array(f.lower), np.array(f.upper)
        sane &= bool((lo >= 0).all() and (lo <= point + 1e-9).all() and (point <= hi + 1e-9).all())
        inside += int(((actual >= lo) & (actual <= hi)).sum())
        total += len(actual)
    return inside / total, sane


@pytest.fixture(scope="module")
def data() -> Collected:
    return asyncio.run(collect())


def test_report(data: Collected) -> None:
    """Run with ``pytest -s -m eval`` to print the numbers for the docs."""
    print()
    print(describe("independent", cases_of(data.independent)))
    print(describe("mock account", cases_of(data.mock_account)))
    print(describe("mock campaign", cases_of(data.mock_campaign)))
    for name, series in (("independent", data.independent), ("mock account", data.mock_account)):
        for model in ("seasonal_naive", "seasonal_mean_4"):
            cov, sane = coverage(series, model)
            print(f"coverage {name:13s} {model:16s} n={len(series)} coverage={cov:.3f} sane={sane}")


def test_rule_a_independent(data: Collected) -> None:
    share, gain = share_and_gain(cases_of(data.independent))
    assert share >= SHARE_ACCOUNT and gain >= GAIN_ACCOUNT, (share, gain)


def test_rule_b_mock_account(data: Collected) -> None:
    share, gain = share_and_gain(cases_of(data.mock_account))
    assert share >= SHARE_ACCOUNT and gain >= GAIN_ACCOUNT, (share, gain)


def test_rule_c_mock_campaign(data: Collected) -> None:
    share, gain = share_and_gain(cases_of(data.mock_campaign))
    assert share >= SHARE_CAMPAIGN and gain >= GAIN_CAMPAIGN, (share, gain)


def test_rule_d_interval_calibration(data: Collected) -> None:
    for name, series in (("independent", data.independent), ("mock account", data.mock_account)):
        cov, sane = coverage(series, "seasonal_mean_4")
        assert sane, f"{name}: an interval was negative or did not contain its point forecast"
        assert COVERAGE_LOW <= cov <= COVERAGE_HIGH, f"{name}: coverage {cov:.3f}"
