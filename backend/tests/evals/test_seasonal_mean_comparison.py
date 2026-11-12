"""Should the default forecast be seasonal_mean_4 instead of seasonal-naive? Pre-registered.

PRE-REGISTERED before any run. Background: on the mock generator the learned models did not beat
seasonal-naive (ML_EVALUATION.md 4b); on the independent generator the plain 4-week seasonal mean
beat it (4d). Two synthetic sources disagree, so the question is asked of both, on seeds used for
nothing else.

Both candidates are point forecasts with no fitting or selection, so there is nothing to tune: for
each series the last 14 days are hidden and each method forecasts them from the earlier history.

Sources and seeds (all fresh):
  independent generator (``independent_series.py``), seeds 121-140: 20 seeds x {spend, sales}
  = 40 cases
  mock generator, seeds 46-60, mature days only, same hold-out protocol as the earlier evaluations:
    account level: 15 seeds x {spend, sales} = 30 cases
    campaign level: 15 seeds x 12 campaigns x {spend, sales} = up to 360 cases

Recommendation rule. Changing the default to seasonal_mean_4 is RECOMMENDED only if ALL hold:
  A  independent: seasonal_mean_4 MAE <= seasonal-naive MAE in >= 70% of cases AND mean relative MAE
     improvement >= 5%
  B  mock account level: the same two bars
  C  mock campaign level: MAE <= seasonal-naive in >= 60% of cases AND mean relative
     improvement >= 0
Otherwise the default stays seasonal-naive. Any mixed outcome is reported as it is; the bars are
not changed after the run. Intervals are not evaluated here: if the rule recommends a change,
interval calibration for the new default must be tested before it ships. Metrics reported: MAE,
RMSE, MAPE, WAPE for both methods. These results concern synthetic data only, not real accounts.
"""

import asyncio
import statistics
from datetime import timedelta
from typing import NamedTuple

import numpy as np
import pytest

from app.application.forecasting.metrics import mae, mape, rmse, wape
from app.application.forecasting.models import SeasonalMean4, SeasonalNaive
from app.domain.ads.models import EntityType
from app.domain.kpi.periods import DateRange
from app.infrastructure.providers.mock.generator import MockConfig, generate
from tests.evals.independent_series import make_series
from tests.evals.test_forecast_eval import END, HORIZON, NOW, ORG
from tests.unit.fake_reader import FakeReader

pytestmark = pytest.mark.eval

INDEPENDENT_SEEDS = list(range(121, 141))
MOCK_SEEDS = list(range(46, 61))
SHARE_ACCOUNT = 0.70
GAIN_ACCOUNT = 0.05
SHARE_CAMPAIGN = 0.60
GAIN_CAMPAIGN = 0.0


class Case(NamedTuple):
    naive: tuple[float, float, float | None, float | None]  # mae, rmse, mape, wape
    mean4: tuple[float, float, float | None, float | None]


def compare(values: np.ndarray, weekday: int) -> Case | None:
    if values.sum() == 0:
        return None
    history, actual = values[:-HORIZON], values[-HORIZON:]
    out = []
    for model in (SeasonalNaive(), SeasonalMean4()):
        f = np.asarray(model.forecast(history, HORIZON, weekday), dtype=float)
        out.append((mae(actual, f), rmse(actual, f), mape(actual, f), wape(actual, f)))
    return Case(out[0], out[1])


def independent_cases() -> list[Case]:
    cases = []
    weekday = (END - timedelta(days=HORIZON)).weekday()
    for seed in INDEPENDENT_SEEDS:
        for values in make_series(seed):
            c = compare(values, weekday)
            if c:
                cases.append(c)
    return cases


async def mock_cases() -> tuple[list[Case], list[Case]]:
    account: list[Case] = []
    campaign: list[Case] = []
    for seed in MOCK_SEEDS:
        data = generate(MockConfig(seed=seed, end_date=END, now=NOW))
        reader = FakeReader(data, ORG, NOW)
        end = END - timedelta(days=3)
        period = DateRange(data.dates[0], end)
        days = [period.start + timedelta(days=i) for i in range(period.days)]
        weekday = (end - timedelta(days=HORIZON)).weekday()

        def to_array(points: list, field: str, days: list = days) -> np.ndarray:  # type: ignore[type-arg]
            by_day = {p.day: getattr(p.totals, field) for p in points}
            return np.array([by_day.get(d, 0) for d in days], dtype=float)

        total = await reader.daily_totals(EntityType.CAMPAIGN, period, include_immature=False)
        for field in ("spend_paise", "sales_paise"):
            c = compare(to_array(total, field), weekday)
            if c:
                account.append(c)
        camps = await reader.campaigns()
        series = await reader.daily_by_entity(
            EntityType.CAMPAIGN, period, [x.id for x in camps], include_immature=False
        )
        for camp in camps:
            mine = [p for p in series if p.entity_id == camp.id]
            for field in ("spend_paise", "sales_paise"):
                c = compare(to_array(mine, field), weekday)
                if c:
                    campaign.append(c)
    return account, campaign


def share_and_gain(cases: list[Case]) -> tuple[float, float]:
    ok = [c for c in cases if c.naive[0] > 0]
    share = sum(c.mean4[0] <= c.naive[0] for c in ok) / len(ok)
    gain = statistics.fmean((c.naive[0] - c.mean4[0]) / c.naive[0] for c in ok)
    return share, gain


def describe(name: str, cases: list[Case]) -> str:
    share, gain = share_and_gain(cases)

    def avg(which: str, i: int) -> float:
        return statistics.fmean(
            getattr(c, which)[i] for c in cases if getattr(c, which)[i] is not None
        )

    return (
        f"{name:22s} n={len(cases):3d} not_worse={share:.2f} gain={gain:+.3f} | "
        f"naive MAE={avg('naive', 0):.0f} RMSE={avg('naive', 1):.0f} MAPE={avg('naive', 2):.3f} "
        f"WAPE={avg('naive', 3):.3f} | mean4 MAE={avg('mean4', 0):.0f} RMSE={avg('mean4', 1):.0f} "
        f"MAPE={avg('mean4', 2):.3f} WAPE={avg('mean4', 3):.3f}"
    )


@pytest.fixture(scope="module")
def independent() -> list[Case]:
    return independent_cases()


@pytest.fixture(scope="module")
def mock() -> tuple[list[Case], list[Case]]:
    return asyncio.run(mock_cases())


def test_report(independent: list[Case], mock: tuple[list[Case], list[Case]]) -> None:
    """Run with ``pytest -s -m eval`` to print the numbers for the docs."""
    print()
    print(describe("independent", independent))
    print(describe("mock account", mock[0]))
    print(describe("mock campaign", mock[1]))


def test_rule_a_independent(independent: list[Case]) -> None:
    share, gain = share_and_gain(independent)
    assert share >= SHARE_ACCOUNT and gain >= GAIN_ACCOUNT


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN, mock seeds 46-60 account level (30 cases): seasonal_mean_4 no worse in 43% (bar "
        "70%), mean per-case MAE gain -3.4% (bar +5%), although pooled MAE/WAPE were slightly "
        "lower (0.138 vs 0.144). Rule B fails, so the default stays seasonal-naive."
    ),
)
def test_rule_b_mock_account(mock: tuple[list[Case], list[Case]]) -> None:
    share, gain = share_and_gain(mock[0])
    assert share >= SHARE_ACCOUNT and gain >= GAIN_ACCOUNT


def test_rule_c_mock_campaign(mock: tuple[list[Case], list[Case]]) -> None:
    share, gain = share_and_gain(mock[1])
    assert share >= SHARE_CAMPAIGN and gain >= GAIN_CAMPAIGN
