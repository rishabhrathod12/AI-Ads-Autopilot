"""Forecasters. Each sees only ``history`` (values up to and including the forecast origin), so
leakage is impossible by construction: there is nothing else to peek at.

``history`` is a daily series; ``last_weekday`` is the weekday (Mon=0) of its final day.

Baselines come first because every model must justify itself against them. The learned models are
deliberately small (a ridge regression and a shallow gradient-boosted ensemble on lag features,
plus damped-trend Holt-Winters): the data is a few months of daily totals, not big data.
"""

import math
import warnings
from typing import ClassVar, Protocol

import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import Ridge
from statsmodels.tsa.holtwinters import ExponentialSmoothing

FloatArr = NDArray[np.float64]
HORIZON = 14
MIN_ORIGIN = 27  # features look back 28 days
MIN_TRAIN_ROWS = 60


class ForecastModelError(RuntimeError):
    """A model could not be fitted on this history; it is excluded, never silently replaced."""


class Forecaster(Protocol):
    name: ClassVar[str]
    is_baseline: ClassVar[bool]

    def forecast(self, history: FloatArr, horizon: int, last_weekday: int) -> FloatArr: ...


def _check(history: FloatArr, minimum: int = 28) -> None:
    if history.ndim != 1 or len(history) < minimum:
        raise ForecastModelError(f"need at least {minimum} days of history, got {len(history)}")
    if not np.isfinite(history).all():
        raise ForecastModelError("history contains non-finite values")


def _weeks_back(h: int) -> int:
    return math.ceil(h / 7)


class Naive:
    name: ClassVar[str] = "naive"
    is_baseline: ClassVar[bool] = True

    def forecast(self, history: FloatArr, horizon: int, last_weekday: int) -> FloatArr:
        _check(history, 1)
        return np.full(horizon, history[-1], dtype=float)


class MovingAverage7:
    name: ClassVar[str] = "moving_average_7"
    is_baseline: ClassVar[bool] = True

    def forecast(self, history: FloatArr, horizon: int, last_weekday: int) -> FloatArr:
        _check(history, 7)
        return np.full(horizon, history[-7:].mean(), dtype=float)


class SeasonalNaive:
    """Same weekday, most recent week: the bar every model has to clear."""

    name: ClassVar[str] = "seasonal_naive"
    is_baseline: ClassVar[bool] = True

    def forecast(self, history: FloatArr, horizon: int, last_weekday: int) -> FloatArr:
        _check(history, 7)
        n = len(history)
        return np.array(
            [history[n - 1 + h - 7 * _weeks_back(h)] for h in range(1, horizon + 1)], dtype=float
        )


class SeasonalMean4:
    """Same weekday averaged over the last four weeks: seasonal-naive with the noise smoothed."""

    name: ClassVar[str] = "seasonal_mean_4"
    is_baseline: ClassVar[bool] = True

    def forecast(self, history: FloatArr, horizon: int, last_weekday: int) -> FloatArr:
        _check(history, 28)
        n = len(history)
        out = []
        for h in range(1, horizon + 1):
            i = n - 1 + h - 7 * _weeks_back(h)
            out.append(np.mean([history[i - 7 * j] for j in range(4)]))
        return np.array(out, dtype=float)


class ETS:
    """Holt-Winters: damped additive trend, additive weekly seasonality."""

    name: ClassVar[str] = "ets"
    is_baseline: ClassVar[bool] = False

    def forecast(self, history: FloatArr, horizon: int, last_weekday: int) -> FloatArr:
        _check(history, 28)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fit = ExponentialSmoothing(
                    np.maximum(history, 0.0),
                    trend="add",
                    damped_trend=True,
                    seasonal="add",
                    seasonal_periods=7,
                    initialization_method="estimated",
                ).fit(optimized=True)
                out = np.asarray(fit.forecast(horizon), dtype=float)
        except Exception as exc:  # statsmodels raises many types on bad fits
            raise ForecastModelError(f"ets failed: {type(exc).__name__}") from exc
        if not np.isfinite(out).all():
            raise ForecastModelError("ets produced non-finite values")
        return np.maximum(out, 0.0)


def _design(
    y: FloatArr, origins: np.ndarray, h: int, last_weekday: int
) -> tuple[FloatArr, FloatArr]:
    """Features for forecasting ``h`` days ahead from each origin, using only data up to it.

    Everything is expressed relative to the trailing 28-day mean, so one model can learn from
    series of any size. Returns (features, trailing 28-day mean).
    """
    n = len(y)
    cs = np.concatenate([[0.0], np.cumsum(y)])
    m28 = (cs[origins + 1] - cs[origins - 27]) / 28.0
    m7 = (cs[origins + 1] - cs[origins - 6]) / 7.0
    i1 = origins + h - 7 * _weeks_back(h)  # latest same-weekday day at or before the origin
    m4 = (y[i1] + y[i1 - 7] + y[i1 - 14] + y[i1 - 21]) / 4.0
    safe = np.where(m28 > 0, m28, np.nan)
    weekday = (last_weekday - (n - 1 - origins) + h) % 7
    features = np.column_stack(
        [
            np.eye(7)[weekday],
            np.full(len(origins), h / HORIZON),
            y[i1] / safe,
            y[i1 - 7] / safe,
            m7 / safe,
            m4 / safe,
        ]
    )
    return features, m28


class _GlobalRegressor:
    """Direct multi-horizon regression trained only on the supplied history."""

    name: ClassVar[str]
    is_baseline: ClassVar[bool] = False

    def _model(self) -> Ridge | GradientBoostingRegressor:
        raise NotImplementedError

    def forecast(self, history: FloatArr, horizon: int, last_weekday: int) -> FloatArr:
        _check(history, MIN_ORIGIN + 1 + 14)
        n = len(history)
        rows_x, rows_y = [], []
        for h in range(1, horizon + 1):
            origins = np.arange(MIN_ORIGIN, n - h)  # target index origin + h must be <= n - 1
            if len(origins) == 0:
                continue
            x, m28 = _design(history, origins, h, last_weekday)
            y = history[origins + h] / np.where(m28 > 0, m28, np.nan)
            ok = np.isfinite(x).all(axis=1) & np.isfinite(y)
            rows_x.append(x[ok])
            rows_y.append(y[ok])
        x_train, y_train = np.vstack(rows_x), np.concatenate(rows_y)
        if len(y_train) < MIN_TRAIN_ROWS:
            raise ForecastModelError(f"only {len(y_train)} training rows")
        model = self._model().fit(x_train, y_train)

        last = np.array([n - 1])
        out = np.zeros(horizon)
        for h in range(1, horizon + 1):
            x, m28 = _design(history, last, h, last_weekday)
            if not np.isfinite(x).all() or m28[0] <= 0:
                continue  # no recent traffic: forecast zero
            out[h - 1] = max(0.0, float(model.predict(x)[0]) * m28[0])
        return out


class RidgeForecaster(_GlobalRegressor):
    name: ClassVar[str] = "ridge"

    def _model(self) -> Ridge:
        return Ridge(alpha=1.0)


class GradientBoostingForecaster(_GlobalRegressor):
    name: ClassVar[str] = "gbm"

    def _model(self) -> GradientBoostingRegressor:
        return GradientBoostingRegressor(
            n_estimators=80, learning_rate=0.05, max_depth=2, subsample=0.8, random_state=0
        )


def default_forecasters() -> list[Forecaster]:
    return [
        Naive(),
        MovingAverage7(),
        SeasonalNaive(),
        SeasonalMean4(),
        ETS(),
        RidgeForecaster(),
        GradientBoostingForecaster(),
    ]
