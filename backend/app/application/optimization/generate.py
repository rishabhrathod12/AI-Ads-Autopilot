"""Deterministic recommendation calculators: bids, budgets and negative keywords.

Every proposal is computed here in plain Python from the analytics already in the system; no model
is involved, and the numbers quoted in ``evidence`` are exactly the inputs the rule used, so a
reviewer can reproduce any proposal. Proposals are guardrailed (``domain.recommendations``); one
that breaks a guardrail is dropped, never emitted. "Expected impact" is a direction, not a
forecast: no money figure is invented.
"""

import dataclasses
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from app.application.analytics.campaigns import HealthStatus, campaign_overview
from app.application.analytics.keywords import FindingLevel, keyword_analysis
from app.application.analytics.thresholds import DEFAULT_TARGET_ACOS_PCT
from app.application.ports.performance_reader import PerformanceReader
from app.domain.ads.models import DataSource, EntityType
from app.domain.kpi.metrics import MetricTotals, acos_pct
from app.domain.kpi.periods import DateRange
from app.domain.recommendations import (
    MAX_BID_CHANGE_PCT,
    MAX_BUDGET_CHANGE_PCT,
    RECOMMENDATION_TTL,
    Recommendation,
    RecType,
    guardrail_violations,
)

# Minimum evidence before a bid is touched, and the smallest change worth proposing.
BID_MIN_CLICKS = 30
BID_MIN_ORDERS = 3
BID_MIN_CHANGE_PCT = Decimal(5)
HIGH_CONFIDENCE_ORDERS = 10

_ID_NAMESPACE = uuid.UUID("5f0d0c1e-8d4a-4f43-9a54-3f6f0d6d9a10")


@dataclass(frozen=True)
class GenerationResult:
    recommendations: list[Recommendation]
    dropped_by_guardrails: int
    period: DateRange


def _round_paise(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _within_cap(current: int, proposed: int, cap_pct: int) -> int:
    """Clamp a rounded value so rounding can never push the change past the cap."""
    lowest = -((-current * (100 - cap_pct)) // 100)  # ceil(current * (100 - cap) / 100)
    highest = (current * (100 + cap_pct)) // 100  # floor(current * (100 + cap) / 100)
    return max(lowest, min(highest, proposed))


def _totals_evidence(t: MetricTotals) -> dict[str, Any]:
    return {
        "clicks": t.clicks,
        "orders": t.orders,
        "spend_paise": t.spend_paise,
        "sales_paise": t.sales_paise,
    }


def _finish(rec: Recommendation, org: UUID) -> Recommendation:
    """Give the recommendation a deterministic id: the same content always gets the same id."""
    return dataclasses.replace(rec, id=uuid.uuid5(_ID_NAMESPACE, f"{org}:{rec.content_hash}"))


def propose_bid_change(
    *,
    org: UUID,
    target_id: str,
    label: str,
    bid_paise: int,
    totals: MetricTotals,
    target_acos_pct: Decimal,
    period: DateRange,
    data_source: str,
    now: datetime,
) -> Recommendation | None:
    """Scale the bid by target / actual ACOS, within the guardrail, if there is enough data."""
    if totals.clicks < BID_MIN_CLICKS or totals.orders < BID_MIN_ORDERS or bid_paise <= 0:
        return None
    actual = acos_pct(totals.spend_paise, totals.sales_paise)
    if actual is None or actual <= 0:
        return None
    factor = min(
        max(target_acos_pct / actual, Decimal(1) - Decimal(MAX_BID_CHANGE_PCT) / 100),
        Decimal(1) + Decimal(MAX_BID_CHANGE_PCT) / 100,
    )
    proposed = _within_cap(bid_paise, _round_paise(Decimal(bid_paise) * factor), MAX_BID_CHANGE_PCT)
    if proposed < 1 or abs(Decimal(proposed - bid_paise) * 100 / bid_paise) < BID_MIN_CHANGE_PCT:
        return None
    direction = "above" if actual > target_acos_pct else "below"
    return _finish(
        Recommendation(
            id=UUID(int=0),
            organization_id=org,
            type=RecType.BID_CHANGE,
            entity_type="target",
            entity_id=target_id,
            entity_label=label,
            current_value=bid_paise,
            proposed_value=proposed,
            proposed_text=None,
            rationale=(
                f"ACOS {actual:.1f}% is {direction} the {target_acos_pct}% target over "
                f"{period.start}..{period.end}. Rule: bid x target/actual ACOS, limited to "
                f"+/-{MAX_BID_CHANGE_PCT}%."
            ),
            evidence={
                "rule": "bid_scale_to_target_acos",
                "period": [period.start.isoformat(), period.end.isoformat()],
                "target_acos_pct": str(target_acos_pct),
                "actual_acos_pct": str(actual),
                **_totals_evidence(totals),
            },
            confidence="high" if totals.orders >= HIGH_CONFIDENCE_ORDERS else "medium",
            expected_impact=(
                f"Moves this target's ACOS toward the {target_acos_pct}% target "
                f"({'lower' if proposed < bid_paise else 'higher'} bid). No result is predicted."
            ),
            data_source=data_source,
            created_at=now,
            expires_at=now + RECOMMENDATION_TTL,
        ),
        org,
    )


async def generate_recommendations(
    reader: PerformanceReader,
    org: UUID,
    requested: DateRange,
    now: datetime,
    *,
    target_acos_pct: Decimal = DEFAULT_TARGET_ACOS_PCT,
) -> GenerationResult:
    """All proposals for the period. Deterministic: same data and ``now`` give the same output."""
    keywords = await keyword_analysis(reader, requested, target_acos_pct=target_acos_pct)
    period = keywords.period
    provenance = await reader.provenance(period)
    source = "MOCK" if DataSource.MOCK in provenance.data_sources else "AMAZON_ADS_API"
    candidates: list[Recommendation] = []

    # 1. Negative keywords: only "confirmed" search-term findings. A confirmed finding means "zero
    # orders would be unlikely by luck at this campaign's rate", so it is a candidate for review.
    for n in keywords.negative_candidates:
        if n.level is not FindingLevel.CONFIRMED:
            continue
        candidates.append(
            _finish(
                Recommendation(
                    id=UUID(int=0),
                    organization_id=org,
                    type=RecType.NEGATIVE_KEYWORD,
                    entity_type="search_term",
                    entity_id=str(n.term.id),
                    entity_label=n.term.term,
                    current_value=None,
                    proposed_value=None,
                    proposed_text=n.term.term,
                    rationale=(
                        f"'{n.term.term}' took {n.totals.clicks} clicks and "
                        f"{n.totals.spend_paise} paise with no orders in {n.campaign_name}; at the "
                        f"campaign's conversion rate that is {n.chance_of_zero_orders_pct:.1f}% "
                        "likely by luck. Candidate for review: a weak converter, not proof the "
                        "term is irrelevant."
                    ),
                    evidence={
                        "rule": "confirmed_zero_order_search_term",
                        "period": [period.start.isoformat(), period.end.isoformat()],
                        "campaign": n.campaign_name,
                        "chance_of_zero_orders_pct": round(n.chance_of_zero_orders_pct, 2),
                        "reference_cvr_pct": str(n.reference.cvr_pct),
                        "reference_scope": n.reference.scope,
                        **_totals_evidence(n.totals),
                    },
                    confidence="high",
                    expected_impact=(
                        "Stops spend on this search term. Orders it might still produce are "
                        "forgone. No result is predicted."
                    ),
                    data_source=source,
                    created_at=now,
                    expires_at=now + RECOMMENDATION_TTL,
                ),
                org,
            )
        )

    # 2. Bids: enabled, non-negative targets with enough clicks and orders.
    targets = {t.id: t for t in await reader.targets()}
    for et in await reader.totals_by_entity(EntityType.TARGET, period, include_immature=False):
        t = targets.get(et.entity_id)
        if t is None or t.state != "enabled" or t.is_negative:
            continue
        rec = propose_bid_change(
            org=org,
            target_id=str(t.id),
            label=t.text,
            bid_paise=t.bid_paise,
            totals=et.totals,
            target_acos_pct=target_acos_pct,
            period=period,
            data_source=source,
            now=now,
        )
        if rec is not None:
            candidates.append(rec)

    # 3. Budgets: raise a healthy campaign that keeps hitting its cap; trim an unhealthy one.
    overview = await campaign_overview(reader, requested, target_acos_pct=target_acos_pct)
    for row in overview.rows:
        if row.info.state != "enabled" or row.info.daily_budget_paise <= 0:
            continue
        if row.health.status is HealthStatus.HEALTHY and row.budget.constrained:
            sign, why = 1, "healthy ACOS and the budget cap is hit on most days"
        elif row.health.status is HealthStatus.UNHEALTHY:
            sign, why = -1, "unhealthy: " + "; ".join(row.health.reasons)
        else:
            continue
        current = row.info.daily_budget_paise
        proposed = _within_cap(
            current,
            _round_paise(Decimal(current) * (100 + sign * MAX_BUDGET_CHANGE_PCT) / 100),
            MAX_BUDGET_CHANGE_PCT,
        )
        candidates.append(
            _finish(
                Recommendation(
                    id=UUID(int=0),
                    organization_id=org,
                    type=RecType.BUDGET_CHANGE,
                    entity_type="campaign",
                    entity_id=str(row.info.id),
                    entity_label=row.info.name,
                    current_value=current,
                    proposed_value=proposed,
                    proposed_text=None,
                    rationale=(
                        f"{row.info.name}: {why}. Daily budget "
                        f"{'raised' if sign > 0 else 'reduced'} by {MAX_BUDGET_CHANGE_PCT}%."
                    ),
                    evidence={
                        "rule": "budget_step_for_health_and_cap",
                        "period": [period.start.isoformat(), period.end.isoformat()],
                        "health": row.health.status.value,
                        "constrained": row.budget.constrained,
                        "utilization_pct": str(row.budget.utilization_pct),
                        "days_at_cap_pct": str(row.budget.days_at_cap_pct),
                        **_totals_evidence(row.totals),
                    },
                    confidence="medium",
                    expected_impact=(
                        "More spend and volume at the current efficiency."
                        if sign > 0
                        else "Less spend on a campaign that is not meeting target. "
                        "No result is predicted."
                    ),
                    data_source=source,
                    created_at=now,
                    expires_at=now + RECOMMENDATION_TTL,
                ),
                org,
            )
        )

    kept = [c for c in candidates if not guardrail_violations(c)]
    kept.sort(key=lambda r: (r.type.value, r.entity_label, r.entity_id))
    return GenerationResult(kept, len(candidates) - len(kept), period)
