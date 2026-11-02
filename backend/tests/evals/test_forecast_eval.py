"""True-holdout evaluation of the forecaster, on seeds 21-25 (used for nothing else).

PRE-REGISTERED before any forecasting code existed.

Protocol (nested, so model selection cannot flatter itself): for every series, the final 14
mature days are hidden. The forecaster sees only the earlier days, runs its own rolling-origin
backtest to pick a model (or fall back to a baseline), and forecasts the hidden 14 days. We then
compare with what really happened and with seasonal-naive (the same weekday last week).

  Account level, spend and sales (10 cases = 5 seeds x 2 metrics)
    * selected model's holdout MAE <= seasonal-naive's holdout MAE in >= 7 of 10 cases
    * mean relative MAE improvement over seasonal-naive >= 5%
    * mean WAPE of the selected forecast <= 15%
  Campaign level, spend and sales (120 cases = 12 campaigns x 2 metrics x 5 seeds)
    * selected model's holdout MAE <= seasonal-naive's in >= 60% of cases
  Intervals (nominal 80%), pooled over all 130 cases x 14 days
    * empirical holdout coverage between 65% and 92%
  Sanity
    * no negative forecast or interval bound; every interval contains its point forecast

"Beat the baseline or say so": when no model clearly beats seasonal-naive in the backtest, the
forecaster falls back to the best baseline and says why. That fallback is a legitimate outcome and
is scored here like any other.

These results validate the implementation against synthetic data with a simple weekly pattern;
they say nothing about real accounts, whose seasonality and promotions are far less regular.
"""

import asyncio
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import numpy as np
import pytest

from app.application.forecasting.metrics import mae, wape
from app.application.forecasting.models import SeasonalNaive
from app.application.forecasting.service import forecast_series
from app.domain.ads.models import EntityType
from app.domain.kpi.periods import DateRange
from app.infrastructure.providers.mock.generator import MockConfig, generate
from tests.unit.fake_reader import FakeReader

pytestmark = pytest.mark.eval

END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, tzinfo=UTC)
ORG = UUID(int=21)
SEEDS = [21, 22, 23, 24, 25]
HORIZON = 14

MIN_ACCOUNT_NOT_WORSE = 7  # of 10
MIN_ACCOUNT_MEAN_IMPROVEMENT = 0.05
MAX_ACCOUNT_MEAN_WAPE = 0.15
MIN_CAMPAIGN_NOT_WORSE_SHARE = 0.60
COVERAGE_RANGE = (0.65, 0.92)


@dataclass(frozen=True)
class Case:
    seed: int
    scope: str  # "account" or a campaign external id
    metric: str
    selected: str
    used_baseline: bool
    selected_mae: float
    seasonal_naive_mae: float
    wape: float | None
    covered: int  # of 14 days inside the interval
    sane: bool


def holdout_case(seed: int, scope: str, metric: str, values: np.ndarray, end: date) -> Case | None:
    """``values``: every mature day of the series, ending on ``end``."""
    if values.sum() == 0:
        return None
    history, actual = values[:-HORIZON], values[-HORIZON:]
    history_end = end - timedelta(days=HORIZON)
    result = forecast_series(history, history_end)
    baseline = SeasonalNaive().forecast(history, HORIZON, history_end.weekday())
    point = np.asarray(result.point, dtype=float)
    lower, upper = np.asarray(result.lower, dtype=float), np.asarray(result.upper, dtype=float)
    return Case(
        seed=seed,
        scope=scope,
        metric=metric,
        selected=result.selected_model,
        used_baseline=result.used_baseline,
        selected_mae=mae(actual, point),
        seasonal_naive_mae=mae(actual, baseline),
        wape=wape(actual, point),
        covered=int(((actual >= lower) & (actual <= upper)).sum()),
        sane=bool((lower >= 0).all() and (point >= lower).all() and (point <= upper).all()),
    )


async def collect_cases() -> list[Case]:
    cases: list[Case] = []
    for seed in SEEDS:
        data = generate(MockConfig(seed=seed, end_date=END, now=NOW))
        reader = FakeReader(data, ORG, NOW)
        end = END - timedelta(days=3)  # last mature day
        period = DateRange(data.dates[0], end)
        days = [period.start + timedelta(days=i) for i in range(period.days)]

        def to_array(points: list, field: str, days: list[date] = days) -> np.ndarray:  # type: ignore[type-arg]
            by_day = {p.day: getattr(p.totals, field) for p in points}
            return np.array([by_day.get(d, 0) for d in days], dtype=float)

        account = await reader.daily_totals(EntityType.CAMPAIGN, period, include_immature=False)
        for metric, field in (("spend", "spend_paise"), ("sales", "sales_paise")):
            case = holdout_case(seed, "account", metric, to_array(account, field), end)
            if case:
                cases.append(case)
        camps = await reader.campaigns()
        series = await reader.daily_by_entity(
            EntityType.CAMPAIGN, period, [c.id for c in camps], include_immature=False
        )
        for c in camps:
            mine = [p for p in series if p.entity_id == c.id]
            for metric, field in (("spend", "spend_paise"), ("sales", "sales_paise")):
                case = holdout_case(seed, c.external_id, metric, to_array(mine, field), end)
                if case:
                    cases.append(case)
    return cases


@pytest.fixture(scope="module")
def cases() -> list[Case]:
    return asyncio.run(collect_cases())


def improvement(c: Case) -> float:
    return 1.0 - c.selected_mae / c.seasonal_naive_mae if c.seasonal_naive_mae > 0 else 0.0


def test_report(cases: list[Case]) -> None:
    """Run with ``pytest -s -m eval`` to print the table for the docs."""
    acct = [c for c in cases if c.scope == "account"]
    camp = [c for c in cases if c.scope != "account"]
    print(f"\naccount cases={len(acct)} campaign cases={len(camp)}")
    for c in acct:
        print(c)
    picks: dict[str, int] = {}
    for c in cases:
        picks[c.selected] = picks.get(c.selected, 0) + 1
    print("model picked (all cases):", picks)
    print("baseline fallbacks:", sum(c.used_baseline for c in cases), "of", len(cases))
    not_worse = [c.selected_mae <= c.seasonal_naive_mae * (1 + 1e-9) for c in cases]
    print(
        "account not worse:", sum(not_worse[i] for i, c in enumerate(cases) if c.scope == "account")
    )
    print(
        "campaign not worse share:",
        sum(n for n, c in zip(not_worse, cases, strict=True) if c.scope != "account") / len(camp),
    )
    print("account mean improvement:", statistics.fmean(improvement(c) for c in acct))
    print("interval coverage:", sum(c.covered for c in cases) / (HORIZON * len(cases)))
    assert len(acct) == 2 * len(SEEDS)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN, first scored run on seeds 21-25: the selected model was no worse than "
        "seasonal-naive in 6 of 10 account cases (needs 7), although mean MAE improvement (7.8%) "
        "and WAPE (12%) met their bars. Four cases were worse, up to -28%: with ~7 overlapping "
        "backtest folds, model selection is noisy. Needs an owner decision and fresh seeds "
        "before any change."
    ),
)
def test_account_level_beats_seasonal_naive(cases: list[Case]) -> None:
    acct = [c for c in cases if c.scope == "account"]
    not_worse = sum(c.selected_mae <= c.seasonal_naive_mae * (1 + 1e-9) for c in acct)
    assert not_worse >= MIN_ACCOUNT_NOT_WORSE
    assert statistics.fmean(improvement(c) for c in acct) >= MIN_ACCOUNT_MEAN_IMPROVEMENT
    assert statistics.fmean(c.wape for c in acct if c.wape is not None) <= MAX_ACCOUNT_MEAN_WAPE


def test_campaign_level_is_usually_not_worse_than_seasonal_naive(cases: list[Case]) -> None:
    camp = [c for c in cases if c.scope != "account"]
    share = sum(c.selected_mae <= c.seasonal_naive_mae * (1 + 1e-9) for c in camp) / len(camp)
    assert share >= MIN_CAMPAIGN_NOT_WORSE_SHARE


def test_interval_coverage_is_honest(cases: list[Case]) -> None:
    coverage = sum(c.covered for c in cases) / (HORIZON * len(cases))
    assert COVERAGE_RANGE[0] <= coverage <= COVERAGE_RANGE[1]


def test_forecasts_are_sane(cases: list[Case]) -> None:
    assert all(c.sane for c in cases)
