"""Campaign analytics: metrics, health status, budget utilisation, spend distribution.

Health is judged on mature days only (the attribution lag would otherwise inflate ACOS) and only
once a campaign has enough clicks. The rules are deliberately simple and documented in
``thresholds.py`` so they can be explained to a seller and tested.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.application.analytics.dashboard import NoDataError
from app.application.analytics.thresholds import (
    BUDGET_CAP_SPEND_SHARE,
    BUDGET_CONSTRAINED_DAYS_SHARE,
    DEFAULT_TARGET_ACOS_PCT,
    MIN_CLICKS_FOR_HEALTH,
    WATCH_ACOS_MULTIPLE,
)
from app.application.ports.performance_reader import (
    CampaignInfo,
    EntityDailyPoint,
    Freshness,
    PerformanceReader,
    Provenance,
)
from app.domain.ads.models import EntityType
from app.domain.kpi.explain import AcosExplanation, explain_acos_change
from app.domain.kpi.metrics import Kpis, MetricTotals, acos_pct
from app.domain.kpi.periods import DateRange


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    WATCH = "watch"
    UNHEALTHY = "unhealthy"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class Health:
    status: HealthStatus
    reasons: tuple[str, ...]


def assess_health(totals: MetricTotals, target_acos_pct: Decimal) -> Health:
    """Pure health rule. See thresholds.py for the numbers and why they were chosen."""
    if totals.clicks < MIN_CLICKS_FOR_HEALTH:
        return Health(
            HealthStatus.INSUFFICIENT_DATA,
            (f"only {totals.clicks} clicks; at least {MIN_CLICKS_FOR_HEALTH} needed to judge",),
        )
    if totals.orders == 0:
        return Health(
            HealthStatus.UNHEALTHY,
            (f"{totals.clicks} clicks and {totals.spend_paise} paise spent with no orders",),
        )
    acos = acos_pct(totals.spend_paise, totals.sales_paise)
    if acos is None:  # orders but no attributed sales: treat as unjudgeable, not as perfect
        return Health(HealthStatus.INSUFFICIENT_DATA, ("orders without attributed sales",))
    if acos <= target_acos_pct:
        return Health(
            HealthStatus.HEALTHY, (f"ACOS {acos:.1f}% is within target {target_acos_pct}%",)
        )
    if acos <= target_acos_pct * WATCH_ACOS_MULTIPLE:
        return Health(HealthStatus.WATCH, (f"ACOS {acos:.1f}% is above target {target_acos_pct}%",))
    return Health(
        HealthStatus.UNHEALTHY,
        (f"ACOS {acos:.1f}% is more than {WATCH_ACOS_MULTIPLE}x target {target_acos_pct}%",),
    )


@dataclass(frozen=True)
class BudgetUse:
    utilization_pct: Decimal | None  # total spend / (days x daily budget)
    days_at_cap_pct: Decimal | None  # share of active days at >= 90% of budget
    constrained: bool


def assess_budget(
    daily_spend_paise: list[int], daily_budget_paise: int, period_days: int
) -> BudgetUse:
    """Uses today's budget for the whole period: budget history is not available."""
    if daily_budget_paise <= 0 or not daily_spend_paise or period_days <= 0:
        return BudgetUse(None, None, False)
    utilization = Decimal(sum(daily_spend_paise) * 100) / Decimal(daily_budget_paise * period_days)
    cap = Decimal(daily_budget_paise) * BUDGET_CAP_SPEND_SHARE
    at_cap = sum(1 for s in daily_spend_paise if Decimal(s) >= cap)
    share = Decimal(at_cap * 100) / Decimal(len(daily_spend_paise))
    return BudgetUse(utilization, share, share >= BUDGET_CONSTRAINED_DAYS_SHARE * 100)


@dataclass(frozen=True)
class CampaignRow:
    info: CampaignInfo
    totals: MetricTotals
    kpis: Kpis
    spend_share_pct: Decimal | None
    health: Health
    budget: BudgetUse


@dataclass(frozen=True)
class SpendShare:
    campaign_id: UUID
    external_id: str
    share_pct: Decimal
    cumulative_pct: Decimal


@dataclass(frozen=True)
class CampaignOverview:
    requested: DateRange
    period: DateRange  # truncated to mature days
    truncated_days: int
    target_acos_pct: Decimal
    rows: list[CampaignRow]
    distribution: list[SpendShare]  # largest first
    freshness: Freshness
    provenance: Provenance


def spend_distribution(rows: list[CampaignRow]) -> list[SpendShare]:
    total = sum(r.totals.spend_paise for r in rows)
    if total == 0:
        return []
    ordered = sorted(rows, key=lambda r: r.totals.spend_paise, reverse=True)
    out, running = [], Decimal(0)
    for r in ordered:
        share = Decimal(r.totals.spend_paise * 100) / Decimal(total)
        running += share
        out.append(SpendShare(r.info.id, r.info.external_id, share, running))
    return out


async def campaign_overview(
    reader: PerformanceReader,
    requested: DateRange,
    *,
    target_acos_pct: Decimal = DEFAULT_TARGET_ACOS_PCT,
) -> CampaignOverview:
    if target_acos_pct <= 0:
        raise ValueError("target ACOS must be positive")
    fresh = await reader.freshness()
    if fresh.latest_date is None:
        raise NoDataError("no advertising data")
    period = requested
    if fresh.mature_through is not None:
        period = requested.truncate_end(fresh.mature_through)  # NoMatureDataError if none

    campaigns = await reader.campaigns()
    totals = {
        t.entity_id: t.totals
        for t in await reader.totals_by_entity(EntityType.CAMPAIGN, period, include_immature=False)
    }
    daily: dict[UUID, list[EntityDailyPoint]] = defaultdict(list)
    for p in await reader.daily_by_entity(
        EntityType.CAMPAIGN, period, [c.id for c in campaigns], include_immature=False
    ):
        daily[p.entity_id].append(p)

    total_spend = sum(t.spend_paise for t in totals.values())
    rows = []
    for c in campaigns:
        t = totals.get(c.id, MetricTotals())
        rows.append(
            CampaignRow(
                info=c,
                totals=t,
                kpis=Kpis.from_totals(t),
                spend_share_pct=(
                    Decimal(t.spend_paise * 100) / Decimal(total_spend) if total_spend else None
                ),
                health=assess_health(t, target_acos_pct),
                budget=assess_budget(
                    [p.totals.spend_paise for p in daily.get(c.id, [])],
                    c.daily_budget_paise,
                    period.days,
                ),
            )
        )
    return CampaignOverview(
        requested=requested,
        period=period,
        truncated_days=requested.days - period.days,
        target_acos_pct=target_acos_pct,
        rows=rows,
        distribution=spend_distribution(rows),
        freshness=fresh,
        provenance=await reader.provenance(period),
    )


@dataclass(frozen=True)
class CampaignChange:
    """One campaign: mature-aligned current vs previous period, with the ACOS driver breakdown."""

    info: CampaignInfo
    current: DateRange
    previous: DateRange
    current_kpis: Kpis
    previous_kpis: Kpis
    acos_explanation: AcosExplanation | None


async def campaign_changes(reader: PerformanceReader, requested: DateRange) -> list[CampaignChange]:
    fresh = await reader.freshness()
    if fresh.latest_date is None:
        raise NoDataError("no advertising data")
    current = requested
    if fresh.mature_through is not None:
        current = requested.truncate_end(fresh.mature_through)
    previous = current.previous()
    campaigns = await reader.campaigns()
    cur = {
        t.entity_id: t.totals
        for t in await reader.totals_by_entity(EntityType.CAMPAIGN, current, include_immature=False)
    }
    prev = {
        t.entity_id: t.totals
        for t in await reader.totals_by_entity(
            EntityType.CAMPAIGN, previous, include_immature=False
        )
    }
    out = []
    for c in campaigns:
        t_cur, t_prev = cur.get(c.id, MetricTotals()), prev.get(c.id, MetricTotals())
        out.append(
            CampaignChange(
                info=c,
                current=current,
                previous=previous,
                current_kpis=Kpis.from_totals(t_cur),
                previous_kpis=Kpis.from_totals(t_prev),
                acos_explanation=explain_acos_change(t_cur, t_prev),
            )
        )
    return out
