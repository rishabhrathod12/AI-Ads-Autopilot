"""Rolling-origin backtest: train on an expanding window, forecast the next 14 days, repeat.

Origins step forward a week at a time, so every fold sees a different slice of the calendar. A
model is only ever shown data up to its origin, so there is no leakage.
"""

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from app.application.forecasting.metrics import Score, score
from app.application.forecasting.models import Forecaster, ForecastModelError

FloatArr = NDArray[np.float64]


@dataclass
class Backtest:
    origins: list[int]  # index of the last training day of each fold
    actuals: list[FloatArr]
    forecasts: dict[str, list[FloatArr]] = field(default_factory=dict)  # per model, per fold
    failed: dict[str, str] = field(default_factory=dict)

    def pooled(self, model: str) -> Score:
        return score(np.concatenate(self.actuals), np.concatenate(self.forecasts[model]))

    def fold_mae(self, model: str, fold: int) -> float:
        return float(np.mean(np.abs(self.actuals[fold] - self.forecasts[model][fold])))


def fold_origins(n: int, horizon: int, min_train: int, step: int) -> list[int]:
    """Indices of the last training day for each fold; every fold has ``horizon`` days to score."""
    first, last = min_train - 1, n - 1 - horizon
    return list(range(first, last + 1, step)) if last >= first else []


def run_backtest(
    values: FloatArr,
    end_weekday: int,
    forecasters: list[Forecaster],
    *,
    horizon: int,
    min_train: int,
    step: int,
) -> Backtest:
    n = len(values)
    origins = fold_origins(n, horizon, min_train, step)
    bt = Backtest(origins=origins, actuals=[values[o + 1 : o + 1 + horizon] for o in origins])
    for model in forecasters:
        preds: list[FloatArr] = []
        try:
            for o in origins:
                weekday = (end_weekday - (n - 1 - o)) % 7
                preds.append(
                    np.asarray(model.forecast(values[: o + 1], horizon, weekday), dtype=float)
                )
        except ForecastModelError as exc:
            bt.failed[model.name] = str(
                exc
            )  # excluded entirely: a partial record is not comparable
            continue
        bt.forecasts[model.name] = preds
    return bt
