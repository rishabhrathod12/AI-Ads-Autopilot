"""Turn forecasts into storable records that carry the evidence behind the model choice."""

from dataclasses import asdict
from datetime import datetime, timedelta
from uuid import UUID

from app.application.forecasting.reports import AccountForecast
from app.application.forecasting.service import SeriesForecast
from app.application.ports.forecast_store import ForecastStore, Metric, NewForecastRun, ScopeType
from app.domain.ads.models import DataSource


def to_run(
    forecast: SeriesForecast,
    *,
    scope_type: ScopeType,
    scope_id: UUID | None,
    metric: Metric,
    data_source: DataSource,
    input_as_of: datetime | None,
) -> NewForecastRun:
    days = [forecast.horizon_start + timedelta(days=i) for i in range(len(forecast.point))]
    return NewForecastRun(
        scope_type=scope_type,
        scope_id=scope_id,
        metric=metric,
        horizon_start=days[0],
        horizon_end=days[-1],
        train_start=forecast.train_end - timedelta(days=forecast.train_days - 1),
        train_end=forecast.train_end,
        train_days=forecast.train_days,
        model=forecast.selected_model,
        used_baseline=forecast.used_baseline,
        params={
            "reason": forecast.reason,
            "interval_level": forecast.interval_level,
            "n_folds": forecast.n_folds,
            "cv_coverage": forecast.cv_coverage,
            "scores": {m: asdict(s) for m, s in forecast.scores.items()},
            "folds_beating_seasonal_naive": forecast.folds_beating_seasonal_naive,
            "failed_models": forecast.failed_models,
        },
        points=[
            {"date": d.isoformat(), "point": p, "lower": lo, "upper": hi}
            for d, p, lo, hi in zip(
                days, forecast.point, forecast.lower, forecast.upper, strict=True
            )
        ],
        data_source=data_source,
        input_as_of=input_as_of,
    )


async def save_account_forecast(store: ForecastStore, result: AccountForecast) -> dict[str, UUID]:
    """Persist the account spend and sales forecasts. ACOS is derived on read, never stored."""
    (source,) = result.provenance.data_sources  # mixed sources must not be stored as one run
    ids: dict[str, UUID] = {}
    pairs: list[tuple[Metric, SeriesForecast]] = [
        ("spend", result.forecasts.spend),
        ("sales", result.forecasts.sales),
    ]
    for metric, series in pairs:
        run = to_run(
            series,
            scope_type="account",
            scope_id=None,
            metric=metric,
            data_source=source,
            input_as_of=result.provenance.as_of,
        )
        ids[metric] = await store.save(run)
    return ids
