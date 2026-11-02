"""Account- and campaign-level forecasts from the analytics reader.

Only mature days train a model. The attribution lag leaves a gap between the last mature day and
the latest data, so the horizon starts after the last mature day and ``gap_days`` says how many
of its first days already have (still incomplete) data.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from app.application.analytics.dashboard import NoDataError
from app.application.forecasting.models import Forecaster
from app.application.forecasting.service import (
    AcosForecast,
    InsufficientHistoryError,
    NoTrafficError,
    SeriesForecast,
    derive_acos,
    forecast_series,
)
from app.application.ports.performance_reader import (
    CampaignInfo,
    DailyPoint,
    EntityDailyPoint,
    Freshness,
    PerformanceReader,
    Provenance,
)
from app.domain.ads.models import EntityType
from app.domain.kpi.periods import DateRange

MAX_HISTORY_DAYS = 180


@dataclass(frozen=True)
class MetricForecasts:
    spend: SeriesForecast
    sales: SeriesForecast
    acos: AcosForecast


@dataclass(frozen=True)
class AccountForecast:
    forecasts: MetricForecasts
    gap_days: int  # days between the last mature day and the latest data
    freshness: Freshness
    provenance: Provenance


@dataclass(frozen=True)
class CampaignForecastRow:
    campaign: CampaignInfo
    forecasts: MetricForecasts


@dataclass(frozen=True)
class CampaignForecasts:
    rows: list[CampaignForecastRow]
    skipped: list[tuple[str, str]]  # (campaign external id, reason)
    gap_days: int
    freshness: Freshness
    provenance: Provenance


def _array(
    points: Sequence[DailyPoint | EntityDailyPoint], field: str, end: date
) -> NDArray[np.float64]:
    """Consecutive daily values from the first day with data to ``end``; quiet days are 0."""
    by_day = {p.day: float(getattr(p.totals, field)) for p in points}
    start = min(by_day)
    return np.array(
        [by_day.get(start + timedelta(days=i), 0.0) for i in range((end - start).days + 1)]
    )


def _both(
    points: Sequence[DailyPoint | EntityDailyPoint],
    end: date,
    horizon: int,
    forecasters: list[Forecaster] | None,
) -> MetricForecasts:
    spend = forecast_series(
        _array(points, "spend_paise", end), end, horizon=horizon, forecasters=forecasters
    )
    sales = forecast_series(
        _array(points, "sales_paise", end), end, horizon=horizon, forecasters=forecasters
    )
    return MetricForecasts(spend, sales, derive_acos(spend, sales))


async def _window(reader: PerformanceReader) -> tuple[Freshness, DateRange, int]:
    fresh = await reader.freshness()
    if fresh.latest_date is None or fresh.mature_through is None:
        raise NoDataError("no advertising data")
    end = fresh.mature_through
    window = DateRange(end - timedelta(days=MAX_HISTORY_DAYS - 1), end)
    return fresh, window, (fresh.latest_date - end).days


async def forecast_account(
    reader: PerformanceReader, *, horizon: int = 14, forecasters: list[Forecaster] | None = None
) -> AccountForecast:
    fresh, window, gap = await _window(reader)
    points = await reader.daily_totals(EntityType.CAMPAIGN, window, include_immature=False)
    if not points:
        raise NoDataError("no mature advertising data")
    return AccountForecast(
        # CPU-bound (seconds): keep it off the event loop so the server stays responsive.
        forecasts=await asyncio.to_thread(_both, points, window.end, horizon, forecasters),
        gap_days=gap,
        freshness=fresh,
        provenance=await reader.provenance(window),
    )


async def forecast_campaigns(
    reader: PerformanceReader, *, horizon: int = 14, forecasters: list[Forecaster] | None = None
) -> CampaignForecasts:
    fresh, window, gap = await _window(reader)
    campaigns = await reader.campaigns()
    points = await reader.daily_by_entity(
        EntityType.CAMPAIGN, window, [c.id for c in campaigns], include_immature=False
    )
    by_campaign: dict[UUID, list[EntityDailyPoint]] = {}
    for p in points:
        by_campaign.setdefault(p.entity_id, []).append(p)
    rows: list[CampaignForecastRow] = []
    skipped: list[tuple[str, str]] = []
    for c in campaigns:
        mine = by_campaign.get(c.id)
        if not mine:
            skipped.append((c.external_id, "no mature data"))
            continue
        try:
            both = await asyncio.to_thread(_both, mine, window.end, horizon, forecasters)
            rows.append(CampaignForecastRow(c, both))
        except (InsufficientHistoryError, NoTrafficError) as exc:
            skipped.append((c.external_id, str(exc)))
    return CampaignForecasts(rows, skipped, gap, fresh, await reader.provenance(window))
