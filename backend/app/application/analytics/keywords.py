"""Keyword and search-term findings: wasted spend, negative-keyword candidates, opportunities.

These are *findings with evidence*, not recommendations: M6 turns them into proposals that need
human approval. Judgement uses mature days only and each rule says how sure it is:

* Waste: zero orders after enough clicks that, at the campaign's own conversion rate, zero orders
  would be unlikely luck (5% = "confirmed", 20% = "watch"). No fixed click cutoff.
* Underperforming: has orders, but too few for its clicks, and ACOS above target.
* Opportunity: a converting term not yet targeted exactly, ranked by the exact 95% lower bound of
  its conversion rate so lucky small samples do not top the list.

Items with fewer than ``MIN_CLICKS_FOR_FINDING`` clicks are never flagged, only counted.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.application.analytics.dashboard import NoDataError
from app.application.analytics.thresholds import (
    DEFAULT_TARGET_ACOS_PCT,
    MIN_CLICKS_FOR_FINDING,
    MIN_CLICKS_FOR_HEALTH,
    OPPORTUNITY_BOUND_ALPHA,
    OPPORTUNITY_MIN_ORDERS,
    UNDERPERFORMING_MAX_CHANCE,
    WASTE_CONFIRMED_MAX_CHANCE,
    WASTE_WATCH_MAX_CHANCE,
)
from app.application.ports.performance_reader import (
    Freshness,
    PerformanceReader,
    Provenance,
    SearchTermInfo,
    TargetInfo,
)
from app.domain.ads.models import EntityType
from app.domain.kpi.metrics import Kpis, MetricTotals, acos_pct, cvr_pct
from app.domain.kpi.periods import DateRange
from app.domain.stats import chance_of_at_most, exact_lower_bound

DAYS_PER_MONTH = 30


class FindingLevel(StrEnum):
    CONFIRMED = "confirmed"  # zero orders would be <= 5% likely by luck
    WATCH = "watch"  # <= 20% likely: keep an eye on it


class Strength(StrEnum):
    STRONG = "strong"  # conversion rate credibly above the campaign average
    MODERATE = "moderate"  # converts and is profitable, but not credibly better than average


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


@dataclass(frozen=True)
class Reference:
    """The conversion rate an item is judged against, and where it came from."""

    cvr: float  # fraction
    cvr_pct: Decimal
    scope: str  # "campaign" or "account"


@dataclass(frozen=True)
class WastedTarget:
    target: TargetInfo
    campaign_name: str
    totals: MetricTotals
    level: FindingLevel
    chance_of_zero_orders_pct: float
    reference: Reference
    projected_monthly_spend_paise: int  # estimate: assumes spend continues at the same rate


@dataclass(frozen=True)
class UnderperformingTarget:
    target: TargetInfo
    campaign_name: str
    totals: MetricTotals
    kpis: Kpis
    chance_pct: float  # chance of this few orders (or fewer) from luck alone
    reference: Reference


@dataclass(frozen=True)
class NegativeCandidate:
    term: SearchTermInfo
    parent_target_text: str | None
    campaign_name: str
    totals: MetricTotals
    level: FindingLevel
    chance_of_zero_orders_pct: float
    reference: Reference
    projected_monthly_spend_paise: int


@dataclass(frozen=True)
class Opportunity:
    term: SearchTermInfo
    parent_target_text: str | None
    campaign_name: str
    totals: MetricTotals
    kpis: Kpis
    cvr_lower_bound_pct: float  # exact 95% lower bound
    strength: Strength
    reference: Reference


@dataclass(frozen=True)
class KeywordAnalysis:
    requested: DateRange
    period: DateRange  # truncated to mature days
    truncated_days: int
    target_acos_pct: Decimal
    wasted_targets: list[WastedTarget]
    underperforming_targets: list[UnderperformingTarget]
    negative_candidates: list[NegativeCandidate]
    opportunities: list[Opportunity]
    low_data_targets: int  # enabled targets with traffic but too few clicks to judge
    low_data_terms: int
    unjudged_campaigns: int  # campaigns with no usable reference conversion rate
    freshness: Freshness
    provenance: Provenance


def _reference(totals: MetricTotals | None, scope: str) -> Reference | None:
    if totals is None or totals.clicks < MIN_CLICKS_FOR_HEALTH or totals.orders == 0:
        return None
    pct = cvr_pct(totals.orders, totals.clicks)
    if pct is None:
        return None
    return Reference(totals.orders / totals.clicks, pct, scope)


def judge_zero_orders(
    totals: MetricTotals, reference: Reference | None
) -> tuple[FindingLevel, float] | None:
    """(level, chance of zero orders by luck) for a zero-order item, or None if not flaggable."""
    if reference is None or totals.orders != 0 or totals.clicks < MIN_CLICKS_FOR_FINDING:
        return None
    chance = chance_of_at_most(0, totals.clicks, reference.cvr)
    if chance <= WASTE_CONFIRMED_MAX_CHANCE:
        return FindingLevel.CONFIRMED, chance
    if chance <= WASTE_WATCH_MAX_CHANCE:
        return FindingLevel.WATCH, chance
    return None


def _monthly(spend_paise: int, period: DateRange) -> int:
    return spend_paise * DAYS_PER_MONTH // period.days


def _order(level: FindingLevel) -> int:
    return 0 if level is FindingLevel.CONFIRMED else 1


async def keyword_analysis(
    reader: PerformanceReader,
    requested: DateRange,
    *,
    target_acos_pct: Decimal = DEFAULT_TARGET_ACOS_PCT,
) -> KeywordAnalysis:
    if target_acos_pct <= 0:
        raise ValueError("target ACOS must be positive")
    fresh = await reader.freshness()
    if fresh.latest_date is None:
        raise NoDataError("no advertising data")
    period = requested
    if fresh.mature_through is not None:
        period = requested.truncate_end(fresh.mature_through)

    campaigns = {c.id: c for c in await reader.campaigns()}
    targets = {t.id: t for t in await reader.targets()}
    terms = await reader.search_terms()
    by_camp = {
        e.entity_id: e.totals
        for e in await reader.totals_by_entity(EntityType.CAMPAIGN, period, include_immature=False)
    }
    target_totals = {
        e.entity_id: e.totals
        for e in await reader.totals_by_entity(EntityType.TARGET, period, include_immature=False)
    }
    term_totals = {
        e.entity_id: e.totals
        for e in await reader.totals_by_entity(
            EntityType.SEARCH_TERM, period, include_immature=False
        )
    }

    account = _reference(MetricTotals.sum(by_camp.values()), "account")
    refs: dict[UUID, Reference | None] = {
        cid: _reference(by_camp.get(cid), "campaign") or account for cid in campaigns
    }
    name = {cid: c.name for cid, c in campaigns.items()}

    live_targets = [t for t in targets.values() if t.state == "enabled" and not t.is_negative]
    exact_texts = {normalize(t.text) for t in live_targets if t.match_type == "exact"}
    all_target_texts = {normalize(t.text) for t in live_targets}
    negated_texts = {normalize(t.text) for t in targets.values() if t.is_negative}

    wasted: list[WastedTarget] = []
    under: list[UnderperformingTarget] = []
    low_targets = 0
    for t in live_targets:
        tot = target_totals.get(t.id)
        if tot is None:
            continue
        if tot.clicks < MIN_CLICKS_FOR_FINDING:
            low_targets += 1
            continue
        ref = refs.get(t.campaign_id)
        judged = judge_zero_orders(tot, ref)
        if judged is not None and ref is not None:
            level, chance = judged
            wasted.append(
                WastedTarget(
                    t, name[t.campaign_id], tot, level, chance * 100, ref,
                    _monthly(tot.spend_paise, period),
                )
            )  # fmt: skip
        elif ref is not None and tot.orders > 0:
            chance = chance_of_at_most(tot.orders, tot.clicks, ref.cvr)
            acos = acos_pct(tot.spend_paise, tot.sales_paise)
            if chance <= UNDERPERFORMING_MAX_CHANCE and acos is not None and acos > target_acos_pct:
                under.append(
                    UnderperformingTarget(
                        t, name[t.campaign_id], tot, Kpis.from_totals(tot), chance * 100, ref
                    )
                )
    wasted.sort(key=lambda w: (_order(w.level), -w.totals.spend_paise, w.target.external_id))
    under.sort(key=lambda u: (-u.totals.spend_paise, u.target.external_id))
    flagged_target_ids = {w.target.id for w in wasted}

    negatives: list[NegativeCandidate] = []
    opportunities: list[Opportunity] = []
    low_terms = 0
    for s in terms:
        tot = term_totals.get(s.id)
        if tot is None:
            continue
        parent = targets.get(s.target_id) if s.target_id else None
        text = normalize(s.term)
        if tot.clicks < MIN_CLICKS_FOR_FINDING:
            low_terms += 1
            continue
        ref = refs.get(s.campaign_id)

        judged = judge_zero_orders(tot, ref)
        own_keyword = text in all_target_texts  # the keyword itself: act on the keyword instead
        parent_flagged = parent is not None and parent.id in flagged_target_ids
        if judged and ref and not own_keyword and not parent_flagged and text not in negated_texts:
            level, chance = judged
            negatives.append(
                NegativeCandidate(
                    s, parent.text if parent else None, name[s.campaign_id], tot, level,
                    chance * 100, ref, _monthly(tot.spend_paise, period),
                )
            )  # fmt: skip

        harvestable = parent is not None and parent.match_type in ("auto", "broad", "phrase")
        acos = acos_pct(tot.spend_paise, tot.sales_paise)
        if (
            harvestable
            and ref is not None
            and tot.orders >= OPPORTUNITY_MIN_ORDERS
            and acos is not None
            and acos <= target_acos_pct
            and text not in exact_texts
            and text not in negated_texts
        ):
            bound = exact_lower_bound(tot.orders, tot.clicks, OPPORTUNITY_BOUND_ALPHA)
            opportunities.append(
                Opportunity(
                    s,
                    parent.text if parent else None,
                    name[s.campaign_id],
                    tot,
                    Kpis.from_totals(tot),
                    bound * 100,
                    Strength.STRONG if bound >= ref.cvr else Strength.MODERATE,
                    ref,
                )
            )
    negatives.sort(key=lambda n: (_order(n.level), -n.totals.spend_paise, n.term.external_id))
    opportunities.sort(key=lambda o: (-o.cvr_lower_bound_pct, o.term.external_id))

    return KeywordAnalysis(
        requested=requested,
        period=period,
        truncated_days=requested.days - period.days,
        target_acos_pct=target_acos_pct,
        wasted_targets=wasted,
        underperforming_targets=under,
        negative_candidates=negatives,
        opportunities=opportunities,
        low_data_targets=low_targets,
        low_data_terms=low_terms,
        unjudged_campaigns=sum(1 for r in refs.values() if r is None),
        freshness=fresh,
        provenance=await reader.provenance(period),
    )
