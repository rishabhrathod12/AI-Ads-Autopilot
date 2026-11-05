"""Account-level forecast re-score on FRESH seeds 26-40, rule and protocol unchanged.

PRE-REGISTERED before any run on these seeds. The first scored run (seeds 21-25,
``test_forecast_eval.py``) stands as the first result: 6 of 10 account cases no worse than
seasonal-naive against a bar of 7, with mean improvement 7.8% and WAPE 12.0%.

Why only a larger sample, not a new rule: a diagnostic on the seen seeds 21-25 (account cases) tried
benchmarking against the better of seasonal-naive and the 4-week seasonal mean, and a 10% margin.
Neither helped (5/10, 6/10, 4/10 not worse), so the weak-benchmark hypothesis is not supported and the
selection rule is NOT changed. A 10-case sample also cannot separate 6/10 from 7/10 (if the true
no-worse rate were 70%, 6 or fewer of 10 would occur about a third of the time).

Same protocol as the first run (final 14 mature days hidden; the forecaster backtests and selects on
earlier days only), same bars, restated for a 30-case sample (15 seeds x spend and sales):

    * selected model's holdout MAE <= seasonal-naive's in >= 70% of the 30 account cases (21 of 30)
    * mean relative MAE improvement over seasonal-naive >= 5%
    * mean WAPE of the selected forecast <= 15%
    * no negative bounds, every interval contains its point forecast

Campaign-level cases and interval coverage passed on seeds 21-25 and are not re-run here (slow).
A criterion changed after this file's first run needs another fresh seed set and an owner decision.

These results validate the implementation on synthetic data with a simple weekly pattern; they say
nothing about real accounts.
"""

import asyncio
import statistics
from datetime import timedelta

import numpy as np
import pytest

from app.domain.ads.models import EntityType
from app.domain.kpi.periods import DateRange
from app.infrastructure.providers.mock.generator import MockConfig, generate
from tests.evals.test_forecast_eval import (
    END,
    MAX_ACCOUNT_MEAN_WAPE,
    MIN_ACCOUNT_MEAN_IMPROVEMENT,
    NOW,
    ORG,
    Case,
    holdout_case,
    improvement,
)
from tests.unit.fake_reader import FakeReader

pytestmark = pytest.mark.eval

FRESH_SEEDS = list(range(26, 41))
MIN_NOT_WORSE_SHARE = 0.70


async def collect() -> list[Case]:
    cases: list[Case] = []
    for seed in FRESH_SEEDS:
        data = generate(MockConfig(seed=seed, end_date=END, now=NOW))
        reader = FakeReader(data, ORG, NOW)
        end = END - timedelta(days=3)
        period = DateRange(data.dates[0], end)
        days = [period.start + timedelta(days=i) for i in range(period.days)]
        account = await reader.daily_totals(EntityType.CAMPAIGN, period, include_immature=False)
        for metric, field in (("spend", "spend_paise"), ("sales", "sales_paise")):
            by_day = {p.day: getattr(p.totals, field) for p in account}
            values = np.array([by_day.get(d, 0) for d in days], dtype=float)
            case = holdout_case(seed, "account", metric, values, end)
            if case:
                cases.append(case)
    return cases


@pytest.fixture(scope="module")
def cases() -> list[Case]:
    return asyncio.run(collect())


def test_report(cases: list[Case]) -> None:
    """Run with ``pytest -s -m eval`` to print the numbers for the docs."""
    for c in cases:
        print(c)
    not_worse = sum(c.selected_mae <= c.seasonal_naive_mae * (1 + 1e-9) for c in cases)
    print("cases:", len(cases), "not worse:", not_worse)
    print("mean improvement:", statistics.fmean(improvement(c) for c in cases))
    print("mean WAPE:", statistics.fmean(c.wape for c in cases if c.wape is not None))
    picks: dict[str, int] = {}
    for c in cases:
        picks[c.selected] = picks.get(c.selected, 0) + 1
    print("models:", picks, "baseline fallbacks:", sum(c.used_baseline for c in cases))
    assert len(cases) == 2 * len(FRESH_SEEDS)


def test_account_not_worse_share(cases: list[Case]) -> None:
    share = sum(c.selected_mae <= c.seasonal_naive_mae * (1 + 1e-9) for c in cases) / len(cases)
    assert share >= MIN_NOT_WORSE_SHARE


def test_account_mean_improvement_and_wape(cases: list[Case]) -> None:
    assert statistics.fmean(improvement(c) for c in cases) >= MIN_ACCOUNT_MEAN_IMPROVEMENT
    assert statistics.fmean(c.wape for c in cases if c.wape is not None) <= MAX_ACCOUNT_MEAN_WAPE


def test_forecasts_are_sane(cases: list[Case]) -> None:
    assert all(c.sane for c in cases)
