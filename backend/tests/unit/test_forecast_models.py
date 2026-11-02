import numpy as np
import pytest

from app.application.forecasting.backtest import fold_origins, run_backtest
from app.application.forecasting.metrics import mae, mape, rmse, score, wape
from app.application.forecasting.models import (
    ETS,
    ForecastModelError,
    GradientBoostingForecaster,
    MovingAverage7,
    Naive,
    RidgeForecaster,
    SeasonalMean4,
    SeasonalNaive,
    default_forecasters,
)

WEEK = np.array([100.0, 110, 120, 130, 160, 190, 140])  # Mon..Sun pattern


def weekly(n: int, trend: float = 0.0, noise: float = 0.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = np.array([WEEK[i % 7] for i in range(n)]) + trend * np.arange(n)
    return np.asarray(base + (rng.normal(0, noise, n) if noise else 0.0), dtype=float)


# ---- metrics -------------------------------------------------------------------------------
def test_metrics_by_hand() -> None:
    a, f = [100, 200, 0, 50], [110, 180, 10, 50]
    assert mae(a, f) == pytest.approx((10 + 20 + 10 + 0) / 4)
    assert rmse(a, f) == pytest.approx(np.sqrt((100 + 400 + 100 + 0) / 4))
    assert mape(a, f) == pytest.approx((0.10 + 0.10 + 0.0) / 3)  # the zero-actual day is skipped
    assert wape(a, f) == pytest.approx(40 / 350)
    s = score(a, f)
    assert (s.mae, s.wape) == (mae(a, f), wape(a, f))


def test_metrics_handle_all_zero_actuals_and_bad_shapes() -> None:
    assert mape([0, 0], [1, 2]) is None
    assert wape([0, 0], [1, 2]) is None
    assert mae([0, 0], [1, 2]) == 1.5
    for bad in (([], []), ([1, 2], [1]), ([[1]], [[1]])):
        with pytest.raises(ValueError):
            mae(*bad)


# ---- baselines -----------------------------------------------------------------------------
def test_seasonal_naive_repeats_the_last_week_exactly() -> None:
    y = weekly(70)
    out = SeasonalNaive().forecast(y, 14, last_weekday=(69) % 7)
    assert np.array_equal(out, weekly(84)[70:84])
    assert out[0] == y[-7]  # day 1 ahead = same weekday last week
    assert out[7] == y[-7]  # day 8 ahead = same weekday, still the last observed week


def test_other_baselines() -> None:
    y = weekly(70, noise=3.0)
    assert np.all(Naive().forecast(y, 14, 0) == y[-1])
    assert np.all(MovingAverage7().forecast(y, 14, 0) == y[-7:].mean())
    sm = SeasonalMean4().forecast(y, 7, 0)
    for h in range(1, 8):
        expected = np.mean([y[len(y) - 1 + h - 7 - 7 * j] for j in range(4)])
        assert sm[h - 1] == pytest.approx(expected)


def test_seasonal_mean_smooths_noise_that_seasonal_naive_repeats() -> None:
    rng_err = []
    for seed in range(20):
        y = weekly(70, noise=15.0, seed=seed)
        truth = weekly(84)[70:84]
        rng_err.append(
            (
                mae(truth, SeasonalNaive().forecast(y, 14, 0)),
                mae(truth, SeasonalMean4().forecast(y, 14, 0)),
            )
        )
    naive_err, mean_err = np.mean(rng_err, axis=0)
    assert mean_err < naive_err


# ---- learned models ------------------------------------------------------------------------
@pytest.mark.parametrize("model", [ETS(), RidgeForecaster(), GradientBoostingForecaster()])
def test_learned_models_recover_a_clean_weekly_pattern(model: object) -> None:
    y = weekly(90, trend=0.3)
    truth = weekly(104, trend=0.3)[90:104]
    out = model.forecast(y, 14, last_weekday=89 % 7)  # type: ignore[attr-defined]
    assert out.shape == (14,)
    assert (out >= 0).all()
    error = wape(truth, out)
    assert error is not None
    assert error < 0.12  # within a few percent on noise-free data


@pytest.mark.parametrize("model", [RidgeForecaster(), GradientBoostingForecaster(), ETS()])
def test_learned_models_are_deterministic(model: object) -> None:
    y = weekly(90, noise=8.0, seed=3)
    a = model.forecast(y, 14, 2)  # type: ignore[attr-defined]
    b = model.forecast(y, 14, 2)  # type: ignore[attr-defined]
    assert np.array_equal(a, b)


@pytest.mark.parametrize("model", default_forecasters())
def test_models_reject_bad_history(model: object) -> None:
    with pytest.raises(ForecastModelError):
        model.forecast(np.array([]), 14, 0)  # type: ignore[attr-defined]
    with pytest.raises(ForecastModelError):
        bad = weekly(60)
        bad[10] = np.nan
        model.forecast(bad, 14, 0)  # type: ignore[attr-defined]


def test_no_recent_traffic_forecasts_zero_not_nan() -> None:
    y = weekly(90)
    y[-28:] = 0.0
    for model in (RidgeForecaster(), GradientBoostingForecaster()):
        out = model.forecast(y, 14, 0)
        assert np.isfinite(out).all()
        assert (out == 0).all()


# ---- backtest mechanics and leakage --------------------------------------------------------
def test_fold_origins() -> None:
    assert fold_origins(117, 14, 60, 7) == [59, 66, 73, 80, 87, 94, 101]
    assert fold_origins(74, 14, 60, 7) == [59]
    assert fold_origins(73, 14, 60, 7) == []


def test_every_model_ignores_data_after_its_origin() -> None:
    """Scrambling the values after day 80 must not change any forecast made from earlier origins."""
    a = weekly(117, trend=0.2, noise=5.0, seed=1)
    b = a.copy()
    b[81:] = np.random.default_rng(99).uniform(0, 1000, len(b) - 81)  # a different "future"
    end_weekday = 116 % 7
    bt_a = run_backtest(a, end_weekday, default_forecasters(), horizon=14, min_train=60, step=7)
    bt_b = run_backtest(b, end_weekday, default_forecasters(), horizon=14, min_train=60, step=7)
    early = [i for i, o in enumerate(bt_a.origins) if o <= 80]
    assert len(early) == 4
    for model in bt_a.forecasts:
        for i in early:
            assert np.array_equal(bt_a.forecasts[model][i], bt_b.forecasts[model][i]), (model, i)


def test_a_model_that_fails_is_excluded_not_silently_replaced() -> None:
    class Broken:
        name = "broken"
        is_baseline = False

        def forecast(self, history: np.ndarray, horizon: int, last_weekday: int) -> np.ndarray:
            raise ForecastModelError("boom")

    bt = run_backtest(
        weekly(100),
        0,
        [SeasonalNaive(), Broken()],  # type: ignore[list-item]
        horizon=14,
        min_train=60,
        step=7,
    )
    assert bt.failed == {"broken": "boom"}
    assert "broken" not in bt.forecasts
    assert "seasonal_naive" in bt.forecasts
