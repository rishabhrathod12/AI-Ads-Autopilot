"""Forecast a daily series: backtest every model, pick honestly, attach calibrated intervals.

Selection rule ("beat the baseline or say so"): a learned model is chosen only if, in the
rolling-origin backtest, its pooled MAE is at least ``MODEL_MARGIN`` better than seasonal-naive
AND it beats seasonal-naive in more than half of the folds. Otherwise the best baseline is used
and the reason is recorded. Intervals come from the chosen model's own out-of-sample relative
errors, and their coverage is itself checked leave-one-fold-out.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
from numpy.typing import NDArray

from app.application.forecasting.backtest import Backtest, run_backtest
from app.application.forecasting.metrics import Score
from app.application.forecasting.models import HORIZON, Forecaster, default_forecasters
from app.domain.kpi.metrics import acos_pct

FloatArr = NDArray[np.float64]
MIN_TRAIN_DAYS = 60
FOLD_STEP_DAYS = 7
MODEL_MARGIN = 0.05  # a model must beat seasonal-naive's MAE by at least 5%
INTERVAL_LEVEL = 0.80
BASELINE = "seasonal_naive"
_BASELINE_NAMES = {"naive", "moving_average_7", "seasonal_naive", "seasonal_mean_4"}


class InsufficientHistoryError(ValueError):
    """Too little mature history to backtest and forecast honestly."""


class NoTrafficError(ValueError):
    """The series is all zeros: nothing to forecast."""


@dataclass(frozen=True)
class SeriesForecast:
    train_end: date
    train_days: int
    horizon_start: date
    point: list[float]
    lower: list[float]
    upper: list[float]
    interval_level: float
    selected_model: str
    used_baseline: bool
    reason: str
    scores: dict[str, Score]  # pooled out-of-sample backtest scores, every model that ran
    folds_beating_seasonal_naive: dict[str, int]
    failed_models: dict[str, str]
    n_folds: int
    cv_coverage: float | None  # leave-one-fold-out coverage of the intervals
    fold_totals: list[tuple[float, float]]  # (actual sum, forecast sum) per fold, selected model


def _relative_errors(actual: FloatArr, forecast: FloatArr) -> FloatArr:
    ok = forecast > 0
    return (actual[ok] - forecast[ok]) / forecast[ok]


def _band(errors: FloatArr, level: float) -> tuple[float, float]:
    tail = (1.0 - level) / 2.0
    lo, hi = np.quantile(errors, [tail, 1.0 - tail])
    return float(lo), float(hi)


def select_model(bt: Backtest, margin: float = MODEL_MARGIN) -> tuple[str, bool, str]:
    """(model name, used_baseline, reason)."""
    pooled = {m: bt.pooled(m).mae for m in bt.forecasts}
    base = pooled[BASELINE]
    wins = {
        m: sum(bt.fold_mae(m, i) < bt.fold_mae(BASELINE, i) for i in range(len(bt.origins)))
        for m in bt.forecasts
    }
    contenders = {
        m: v
        for m, v in pooled.items()
        if m in bt.forecasts
        and v <= (1.0 - margin) * base
        and wins[m] * 2 > len(bt.origins)
        and m not in _BASELINE_NAMES
    }
    if contenders:
        best = min(contenders, key=lambda m: contenders[m])
        gain = (1.0 - pooled[best] / base) * 100
        return (
            best,
            False,
            (
                f"{best} beat seasonal-naive by {gain:.0f}% MAE and won "
                f"{wins[best]}/{len(bt.origins)} backtest folds"
            ),
        )
    best_baseline = min((m for m in pooled if m in _BASELINE_NAMES), key=lambda m: pooled[m])
    return (
        best_baseline,
        True,
        (
            "no learned model beat seasonal-naive by "
            f"{margin * 100:.0f}% in the backtest; using the best baseline ({best_baseline})"
        ),
    )


def forecast_series(
    values: FloatArr | list[float],
    end_date: date,
    *,
    horizon: int = HORIZON,
    min_train: int = MIN_TRAIN_DAYS,
    step: int = FOLD_STEP_DAYS,
    level: float = INTERVAL_LEVEL,
    forecasters: list[Forecaster] | None = None,
) -> SeriesForecast:
    """``values``: consecutive daily MATURE values ending on ``end_date`` (missing days as 0)."""
    y = np.asarray(values, dtype=float)
    if not np.isfinite(y).all() or (y < 0).any():
        raise ValueError("values must be finite and non-negative")
    if len(y) < min_train + horizon:
        raise InsufficientHistoryError(
            f"need at least {min_train + horizon} mature days, got {len(y)}"
        )
    if y.sum() == 0:
        raise NoTrafficError("series has no traffic")

    models = forecasters if forecasters is not None else default_forecasters()
    bt = run_backtest(
        y, end_date.weekday(), models, horizon=horizon, min_train=min_train, step=step
    )
    if BASELINE not in bt.forecasts:
        raise InsufficientHistoryError("baseline could not be evaluated")
    name, used_baseline, reason = select_model(bt)

    # Intervals from out-of-sample relative errors of the chosen model.
    folds = len(bt.origins)
    errors = [_relative_errors(bt.actuals[i], bt.forecasts[name][i]) for i in range(folds)]
    lo, hi = _band(np.concatenate(errors), level)
    covered = total = 0
    for i in range(folds):
        others = (
            np.concatenate([e for j, e in enumerate(errors) if j != i]) if folds > 1 else errors[i]
        )
        flo, fhi = _band(others, level)
        f, a = bt.forecasts[name][i], bt.actuals[i]
        covered += int(((a >= f * (1 + flo)) & (a <= f * (1 + fhi))).sum())
        total += len(a)

    chosen = next(m for m in models if m.name == name)
    point = np.asarray(chosen.forecast(y, horizon, end_date.weekday()), dtype=float)
    lower, upper = np.maximum(0.0, point * (1 + lo)), np.maximum(point, point * (1 + hi))
    lower = np.minimum(lower, point)

    return SeriesForecast(
        train_end=end_date,
        train_days=len(y),
        horizon_start=end_date + timedelta(days=1),
        point=point.tolist(),
        lower=lower.tolist(),
        upper=upper.tolist(),
        interval_level=level,
        selected_model=name,
        used_baseline=used_baseline,
        reason=reason,
        scores={m: bt.pooled(m) for m in bt.forecasts},
        folds_beating_seasonal_naive={
            m: sum(bt.fold_mae(m, i) < bt.fold_mae(BASELINE, i) for i in range(folds))
            for m in bt.forecasts
        },
        failed_models=dict(bt.failed),
        n_folds=folds,
        cv_coverage=covered / total if total else None,
        fold_totals=[
            (float(bt.actuals[i].sum()), float(bt.forecasts[name][i].sum())) for i in range(folds)
        ],
    )


@dataclass(frozen=True)
class AcosForecast:
    """ACOS over the horizon, derived from the spend and sales forecasts (never forecast alone)."""

    point_pct: Decimal | None
    low_pct: float | None  # indicative: from how far past horizon-total ACOS forecasts were off
    high_pct: float | None
    n_folds: int


def derive_acos(spend: SeriesForecast, sales: SeriesForecast) -> AcosForecast:
    spend_total, sales_total = round(sum(spend.point)), round(sum(sales.point))
    point = acos_pct(spend_total, sales_total)
    ratios = []
    for (sp_act, sp_fc), (sa_act, sa_fc) in zip(spend.fold_totals, sales.fold_totals, strict=True):
        if min(sp_fc, sa_fc, sa_act) > 0:
            ratios.append((sp_act / sa_act) / (sp_fc / sa_fc))
    if point is None or len(ratios) < 3:
        return AcosForecast(point, None, None, len(ratios))
    lo, hi = np.quantile(ratios, [0.1, 0.9])
    return AcosForecast(point, float(point) * float(lo), float(point) * float(hi), len(ratios))
