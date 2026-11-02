"""Forecast accuracy metrics. Fractions, not percentages (0.10 = 10%)."""

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike


def _pair(actual: ArrayLike, forecast: ArrayLike) -> tuple[np.ndarray, np.ndarray]:
    a, f = np.asarray(actual, dtype=float), np.asarray(forecast, dtype=float)
    if a.shape != f.shape or a.ndim != 1 or a.size == 0:
        raise ValueError("actual and forecast must be non-empty 1-D arrays of equal length")
    return a, f


def mae(actual: ArrayLike, forecast: ArrayLike) -> float:
    a, f = _pair(actual, forecast)
    return float(np.mean(np.abs(a - f)))


def rmse(actual: ArrayLike, forecast: ArrayLike) -> float:
    a, f = _pair(actual, forecast)
    return float(np.sqrt(np.mean((a - f) ** 2)))


def mape(actual: ArrayLike, forecast: ArrayLike) -> float | None:
    """Mean absolute percentage error, skipping days with zero actuals (where it is undefined).
    Unstable near zero, which is why WAPE is reported alongside."""
    a, f = _pair(actual, forecast)
    keep = a != 0
    if not keep.any():
        return None
    return float(np.mean(np.abs((a[keep] - f[keep]) / a[keep])))


def wape(actual: ArrayLike, forecast: ArrayLike) -> float | None:
    """Weighted absolute percentage error: total absolute error / total actual."""
    a, f = _pair(actual, forecast)
    total = float(np.sum(np.abs(a)))
    return None if total == 0 else float(np.sum(np.abs(a - f)) / total)


@dataclass(frozen=True)
class Score:
    mae: float
    rmse: float
    mape: float | None
    wape: float | None


def score(actual: ArrayLike, forecast: ArrayLike) -> Score:
    return Score(
        mae(actual, forecast),
        rmse(actual, forecast),
        mape(actual, forecast),
        wape(actual, forecast),
    )
