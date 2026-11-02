from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import numpy as np
import pytest

from app.application.analytics.dashboard import NoDataError
from app.application.forecasting.backtest import Backtest
from app.application.forecasting.models import (
    Forecaster,
    MovingAverage7,
    Naive,
    SeasonalMean4,
    SeasonalNaive,
    default_forecasters,
)
from app.application.forecasting.reports import forecast_account, forecast_campaigns
from app.application.forecasting.service import (
    InsufficientHistoryError,
    NoTrafficError,
    SeriesForecast,
    derive_acos,
    forecast_series,
    select_model,
)
from app.application.ports.performance_reader import EntityDailyPoint, Freshness
from app.domain.kpi.metrics import acos_pct
from app.infrastructure.providers.mock.generator import GeneratedData, MockConfig, generate
from tests.unit.fake_reader import FakeReader

END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
ORG = UUID("55555555-5555-4555-8555-555555555555")
MATURE_END = END - timedelta(days=3)
WEEK = np.array([100.0, 110, 120, 130, 160, 190, 140])


def weekly(n: int, noise: float = 0.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = np.array([WEEK[i % 7] for i in range(n)])
    return np.asarray(base + (rng.normal(0, noise, n) if noise else 0.0), dtype=float)


# ---- selection rule ------------------------------------------------------------------------
def make_backtest(errors: dict[str, list[float]]) -> Backtest:
    """Each model's per-fold constant forecast error against a flat actual of 100."""
    folds = len(next(iter(errors.values())))
    bt = Backtest(origins=list(range(folds)), actuals=[np.full(14, 100.0) for _ in range(folds)])
    for model, errs in errors.items():
        bt.forecasts[model] = [np.full(14, 100.0 + e) for e in errs]
    return bt


def test_a_model_that_clearly_wins_is_selected() -> None:
    bt = make_backtest(
        {"seasonal_naive": [10] * 5, "naive": [12] * 5, "ets": [6] * 5, "ridge": [9] * 5}
    )
    name, baseline, reason = select_model(bt)
    assert (name, baseline) == ("ets", False)
    assert "40%" in reason
    assert "5/5" in reason


def test_a_marginal_gain_falls_back_to_the_best_baseline_and_says_why() -> None:
    bt = make_backtest({"seasonal_naive": [10] * 5, "naive": [9.5] * 5, "ets": [9.7] * 5})
    name, baseline, reason = select_model(bt)
    assert (name, baseline) == ("naive", True)
    assert "no learned model beat seasonal-naive" in reason


def test_a_model_that_wins_on_average_but_not_in_most_folds_is_rejected() -> None:
    # one huge baseline miss makes `ets` look good pooled, but it loses 4 of 5 folds
    bt = make_backtest({"seasonal_naive": [8, 8, 8, 8, 60], "ets": [9, 9, 9, 9, 10]})
    name, baseline, _ = select_model(bt)
    assert (name, baseline) == ("seasonal_naive", True)


def test_baselines_are_never_reported_as_learned_models() -> None:
    bt = make_backtest({"seasonal_naive": [10] * 5, "seasonal_mean_4": [5] * 5})
    name, baseline, _ = select_model(bt)
    assert name == "seasonal_mean_4"
    assert baseline is True


# ---- forecast_series -----------------------------------------------------------------------
def test_forecast_shape_and_interval_ordering() -> None:
    y = weekly(100, noise=10.0, seed=2)
    f = forecast_series(y, END)
    assert len(f.point) == len(f.lower) == len(f.upper) == 14
    assert f.horizon_start == END + timedelta(days=1)
    assert f.train_days == 100
    assert all(lo >= 0 for lo in f.lower)
    assert all(lo <= p <= hi for lo, p, hi in zip(f.lower, f.point, f.upper, strict=True))
    assert f.n_folds == 4  # origins at days 59, 66, 73, 80 of 100
    assert f.interval_level == 0.8
    assert set(f.scores) >= {"seasonal_naive", "naive", "ets", "ridge", "gbm"}
    assert f.failed_models == {}


def test_noisy_weekly_series_picks_a_smoothing_model_over_seasonal_naive() -> None:
    f = forecast_series(weekly(110, noise=14.0, seed=5), END)
    assert f.selected_model != "seasonal_naive"
    assert f.scores[f.selected_model].mae < f.scores["seasonal_naive"].mae


def test_intervals_are_roughly_calibrated_on_stationary_noise() -> None:
    covs = [forecast_series(weekly(117, noise=12.0, seed=s), END).cv_coverage for s in range(4)]
    assert all(c is not None for c in covs)
    mean_cov = float(np.mean([c for c in covs if c is not None]))
    assert 0.68 <= mean_cov <= 0.92  # nominal 80%, leave-one-fold-out


def test_forecast_is_deterministic() -> None:
    y = weekly(100, noise=9.0, seed=4)
    assert forecast_series(y, END) == forecast_series(y, END)


def test_flat_series_forecasts_flat() -> None:
    f = forecast_series(np.full(100, 500.0), END)
    assert all(p == pytest.approx(500.0, rel=0.02) for p in f.point)


def test_refuses_short_history_empty_traffic_and_bad_values() -> None:
    with pytest.raises(InsufficientHistoryError, match="at least 74"):
        forecast_series(weekly(73), END)
    with pytest.raises(NoTrafficError):
        forecast_series(np.zeros(100), END)
    for bad in (np.append(weekly(100), np.nan), -weekly(100)):
        with pytest.raises(ValueError, match="finite and non-negative"):
            forecast_series(bad, END)


def test_a_failing_model_is_reported_and_excluded() -> None:
    class Broken:
        name = "broken"
        is_baseline = False

        def forecast(self, h: np.ndarray, horizon: int, wd: int) -> np.ndarray:
            from app.application.forecasting.models import ForecastModelError

            raise ForecastModelError("nope")

    models = [Naive(), MovingAverage7(), SeasonalNaive(), SeasonalMean4(), Broken()]
    f = forecast_series(weekly(100, noise=5.0), END, forecasters=models)  # type: ignore[arg-type]
    assert f.failed_models == {"broken": "nope"}
    assert "broken" not in f.scores
    assert f.used_baseline


# ---- ACOS ----------------------------------------------------------------------------------
def fake_forecast(point: list[float], totals: list[tuple[float, float]]) -> SeriesForecast:
    f = forecast_series(weekly(100, noise=5.0), END, forecasters=default_forecasters()[:4])
    return SeriesForecast(**{**f.__dict__, "point": point, "fold_totals": totals})


def test_acos_is_derived_from_the_spend_and_sales_forecasts() -> None:
    spend = fake_forecast([100.0] * 14, [(1400, 1400)] * 5)
    sales = fake_forecast([500.0] * 14, [(7000, 7000)] * 5)
    a = derive_acos(spend, sales)
    assert a.point_pct == acos_pct(1400, 7000) == Decimal(20)
    assert a.n_folds == 5
    assert a.low_pct == pytest.approx(20.0)  # perfect past forecasts: no widening
    assert a.high_pct == pytest.approx(20.0)


def test_acos_interval_widens_with_past_misses_and_is_withheld_with_few_folds() -> None:
    spend = fake_forecast(
        [100.0] * 14, [(1400, 1400), (1680, 1400), (1120, 1400), (1400, 1400), (1400, 1400)]
    )
    sales = fake_forecast([500.0] * 14, [(7000, 7000)] * 5)
    a = derive_acos(spend, sales)
    assert a.low_pct is not None
    assert a.high_pct is not None
    assert a.low_pct < 20 < a.high_pct
    few = derive_acos(
        fake_forecast([100.0] * 14, [(1, 1), (1, 1)]), fake_forecast([500.0] * 14, [(1, 1), (1, 1)])
    )
    assert few.low_pct is None
    assert few.high_pct is None


def test_acos_is_undefined_without_forecast_sales() -> None:
    a = derive_acos(fake_forecast([100.0] * 14, []), fake_forecast([0.0] * 14, []))
    assert a.point_pct is None


# ---- reader-facing wrappers ----------------------------------------------------------------
@pytest.fixture(scope="module")
def data() -> GeneratedData:
    return generate(
        MockConfig(
            end_date=END, now=NOW, n_campaigns=6, ad_groups_per_campaign=2, keywords_per_ad_group=10
        )
    )


BASELINES: list[Forecaster] = [Naive(), MovingAverage7(), SeasonalNaive(), SeasonalMean4()]


async def test_account_forecast_uses_mature_days_and_starts_after_them(data: GeneratedData) -> None:
    calls: list[bool] = []

    class Spy(FakeReader):
        async def daily_totals(self, *a: object, **k: object):  # type: ignore[no-untyped-def]
            calls.append(bool(k.get("include_immature", True)))
            return await super().daily_totals(*a, **k)  # type: ignore[arg-type]

    r = await forecast_account(Spy(data, ORG, NOW), forecasters=BASELINES)
    assert calls == [False]  # only mature days were requested
    assert r.gap_days == 3
    assert r.forecasts.spend.horizon_start == MATURE_END + timedelta(days=1)
    assert r.forecasts.spend.train_end == MATURE_END
    assert r.provenance.as_of == NOW
    assert r.freshness.mature_through == MATURE_END
    expected = acos_pct(round(sum(r.forecasts.spend.point)), round(sum(r.forecasts.sales.point)))
    assert r.forecasts.acos.point_pct == expected


async def test_account_forecast_with_the_full_model_set(data: GeneratedData) -> None:
    r = await forecast_account(FakeReader(data, ORG, NOW))
    assert set(r.forecasts.spend.scores) >= {"ets", "ridge", "gbm", "seasonal_naive"}
    assert r.forecasts.spend.selected_model in r.forecasts.spend.scores


async def test_campaign_forecasts_skip_what_they_cannot_honestly_forecast(
    data: GeneratedData,
) -> None:
    class ShortHistory(FakeReader):
        async def daily_by_entity(self, *a: object, **k: object) -> list[EntityDailyPoint]:
            rows = await super().daily_by_entity(*a, **k)  # type: ignore[arg-type]
            first = (await self.campaigns())[0].id
            cutoff = MATURE_END - timedelta(days=20)
            return [p for p in rows if p.entity_id != first or p.day >= cutoff]

    r = await forecast_campaigns(ShortHistory(data, ORG, NOW), forecasters=BASELINES)
    assert len(r.rows) == len(data.campaigns) - 1
    assert len(r.skipped) == 1
    assert "at least 74" in r.skipped[0][1]


async def test_empty_account_raises(data: GeneratedData) -> None:
    class Empty(FakeReader):
        async def freshness(self) -> Freshness:
            return Freshness(None, None)

    with pytest.raises(NoDataError):
        await forecast_account(Empty(data, ORG, NOW))
    with pytest.raises(NoDataError):
        await forecast_campaigns(Empty(data, ORG, NOW))
