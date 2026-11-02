"""Plain performance tables for keywords/targets and search terms (no judgement applied)."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.application.analytics.dashboard import NoDataError
from app.application.analytics.thresholds import MIN_CLICKS_FOR_FINDING
from app.application.ports.performance_reader import (
    Freshness,
    PerformanceReader,
    Provenance,
    SearchTermInfo,
    TargetInfo,
)
from app.domain.ads.models import EntityType
from app.domain.kpi.metrics import Kpis, MetricTotals
from app.domain.kpi.periods import DateRange

SortKey = Literal["spend", "sales", "clicks", "orders", "acos"]


@dataclass(frozen=True)
class TargetRow:
    target: TargetInfo
    campaign_name: str
    totals: MetricTotals
    kpis: Kpis
    low_data: bool  # too few clicks for the ratios to mean much


@dataclass(frozen=True)
class SearchTermRow:
    term: SearchTermInfo
    campaign_name: str
    totals: MetricTotals
    kpis: Kpis
    low_data: bool


@dataclass(frozen=True)
class TargetTable:
    period: DateRange
    truncated_days: int
    total_rows: int  # before ``limit``
    rows: list[TargetRow]
    freshness: Freshness
    provenance: Provenance


@dataclass(frozen=True)
class SearchTermTable:
    period: DateRange
    truncated_days: int
    total_rows: int
    rows: list[SearchTermRow]
    freshness: Freshness
    provenance: Provenance


def _sort_value(totals: MetricTotals, kpis: Kpis, key: SortKey) -> float:
    if key == "acos":  # undefined ACOS (no sales) sorts as worst, not best
        return float("inf") if kpis.acos_pct is None else float(kpis.acos_pct)
    return float(
        {
            "spend": totals.spend_paise,
            "sales": totals.sales_paise,
            "clicks": totals.clicks,
            "orders": totals.orders,
        }[key]
    )


async def _period(reader: PerformanceReader, requested: DateRange) -> tuple[DateRange, Freshness]:
    fresh = await reader.freshness()
    if fresh.latest_date is None:
        raise NoDataError("no advertising data")
    period = requested
    if fresh.mature_through is not None:
        period = requested.truncate_end(fresh.mature_through)
    return period, fresh


async def target_table(
    reader: PerformanceReader, requested: DateRange, *, sort: SortKey = "spend", limit: int = 50
) -> TargetTable:
    period, fresh = await _period(reader, requested)
    names = {c.id: c.name for c in await reader.campaigns()}
    totals = {
        e.entity_id: e.totals
        for e in await reader.totals_by_entity(EntityType.TARGET, period, include_immature=False)
    }
    rows = []
    for t in await reader.targets():
        tot = totals.get(t.id)
        if tot is None:
            continue
        rows.append(
            TargetRow(
                t,
                names[t.campaign_id],
                tot,
                Kpis.from_totals(tot),
                tot.clicks < MIN_CLICKS_FOR_FINDING,
            )
        )
    rows.sort(key=lambda r: (-_sort_value(r.totals, r.kpis, sort), r.target.external_id))
    return TargetTable(
        period,
        requested.days - period.days,
        len(rows),
        rows[:limit],
        fresh,
        await reader.provenance(period),
    )


async def search_term_table(
    reader: PerformanceReader, requested: DateRange, *, sort: SortKey = "spend", limit: int = 50
) -> SearchTermTable:
    period, fresh = await _period(reader, requested)
    names = {c.id: c.name for c in await reader.campaigns()}
    totals: dict[UUID, MetricTotals] = {
        e.entity_id: e.totals
        for e in await reader.totals_by_entity(
            EntityType.SEARCH_TERM, period, include_immature=False
        )
    }
    rows = []
    for s in await reader.search_terms():
        tot = totals.get(s.id)
        if tot is None:
            continue
        rows.append(
            SearchTermRow(
                s,
                names[s.campaign_id],
                tot,
                Kpis.from_totals(tot),
                tot.clicks < MIN_CLICKS_FOR_FINDING,
            )
        )
    rows.sort(key=lambda r: (-_sort_value(r.totals, r.kpis, sort), r.term.external_id))
    return SearchTermTable(
        period,
        requested.days - period.days,
        len(rows),
        rows[:limit],
        fresh,
        await reader.provenance(period),
    )
