"""Account-level dashboard: totals, daily trend and mature-aligned period comparison.

Display vs judgement: totals and the trend cover every day in the requested period (the latest
days are flagged immature). The comparison uses mature days only, so the attribution lag is never
mistaken for a real ACOS rise.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from app.application.analytics.thresholds import DEFAULT_WINDOW_DAYS
from app.application.ports.performance_reader import (
    DailyPoint,
    Freshness,
    PerformanceReader,
    Provenance,
)
from app.domain.ads.models import EntityType
from app.domain.kpi.explain import AcosExplanation, explain_acos_change
from app.domain.kpi.metrics import KpiChange, Kpis, MetricTotals, compare_totals
from app.domain.kpi.periods import DateRange, NoMatureDataError


class NoDataError(LookupError):
    """The tenant has no advertising data to report on."""


@dataclass(frozen=True)
class Comparison:
    current: DateRange  # truncated to mature days
    previous: DateRange  # equal length, immediately before `current`
    truncated_days: int
    current_totals: MetricTotals
    previous_totals: MetricTotals
    changes: dict[str, KpiChange]
    acos_explanation: AcosExplanation | None


@dataclass(frozen=True)
class DashboardSummary:
    period: DateRange
    totals: MetricTotals
    kpis: Kpis
    immature_days: int
    comparison: Comparison | None  # None when the period has no mature days
    trend: list[DailyPoint]
    freshness: Freshness
    provenance: Provenance


def sum_points(points: list[DailyPoint]) -> MetricTotals:
    return MetricTotals.sum(p.totals for p in points)


async def compare_with_previous(
    reader: PerformanceReader,
    period: DateRange,
    mature_through: date | None,
    *,
    entity_type: EntityType = EntityType.CAMPAIGN,
) -> Comparison | None:
    """Mature-only comparison of ``period`` with the equal-length period before it."""
    effective = period
    if mature_through is not None:
        try:
            effective = period.truncate_end(mature_through)
        except NoMatureDataError:
            return None
    previous = effective.previous()
    cur = sum_points(await reader.daily_totals(entity_type, effective, include_immature=False))
    prev = sum_points(await reader.daily_totals(entity_type, previous, include_immature=False))
    return Comparison(
        current=effective,
        previous=previous,
        truncated_days=period.days - effective.days,
        current_totals=cur,
        previous_totals=prev,
        changes=compare_totals(cur, prev),
        acos_explanation=explain_acos_change(cur, prev),
    )


async def dashboard_summary(
    reader: PerformanceReader, period: DateRange | None = None
) -> DashboardSummary:
    fresh = await reader.freshness()
    if fresh.latest_date is None:
        raise NoDataError("no advertising data")
    if period is None:
        period = DateRange(
            fresh.latest_date - timedelta(days=DEFAULT_WINDOW_DAYS - 1), fresh.latest_date
        )
    trend = await reader.daily_totals(EntityType.CAMPAIGN, period, include_immature=True)
    totals = sum_points(trend)
    return DashboardSummary(
        period=period,
        totals=totals,
        kpis=Kpis.from_totals(totals),
        immature_days=sum(1 for p in trend if not p.is_mature),
        comparison=await compare_with_previous(reader, period, fresh.mature_through),
        trend=trend,
        freshness=fresh,
        provenance=await reader.provenance(period),
    )
