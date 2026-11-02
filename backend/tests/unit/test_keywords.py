"""Keyword/search-term findings: rule boundaries, invariants, and exclusions."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.application.analytics.dashboard import NoDataError
from app.application.analytics.keywords import (
    FindingLevel,
    KeywordAnalysis,
    Reference,
    Strength,
    judge_zero_orders,
    keyword_analysis,
    normalize,
)
from app.application.analytics.thresholds import (
    MIN_CLICKS_FOR_FINDING,
    OPPORTUNITY_MIN_ORDERS,
)
from app.application.ports.performance_reader import Freshness, TargetInfo
from app.domain.kpi.metrics import MetricTotals, acos_pct
from app.domain.kpi.periods import DateRange
from app.domain.stats import chance_of_at_most, exact_lower_bound
from app.infrastructure.providers.mock.generator import GeneratedData, MockConfig, generate
from tests.unit.fake_reader import FakeReader

D = Decimal
END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
ORG = UUID("33333333-3333-4333-8333-333333333333")
FULL = DateRange(END - timedelta(days=119), END)
REF9 = Reference(cvr=0.09, cvr_pct=D(9), scope="campaign")


def totals(clicks: int, orders: int = 0, spend: int = 0, sales: int = 0) -> MetricTotals:
    return MetricTotals(clicks * 100, clicks, spend, sales, orders, orders)


# ---- pure rule boundaries ------------------------------------------------------------------
@pytest.mark.parametrize(
    ("clicks", "expected"),
    [
        (9, None),  # below the minimum evidence, never flagged
        (10, None),  # 0.91^10 = 39%: plausibly just luck
        (17, None),  # 0.91^17 = 20.1%: just outside "watch"
        (18, FindingLevel.WATCH),  # 0.91^18 = 18.3%
        (31, FindingLevel.WATCH),  # 0.91^31 = 5.4%: just outside "confirmed"
        (32, FindingLevel.CONFIRMED),  # 0.91^32 = 4.9%
        (200, FindingLevel.CONFIRMED),
    ],
)
def test_zero_order_levels_at_a_9_percent_reference(
    clicks: int, expected: FindingLevel | None
) -> None:
    result = judge_zero_orders(totals(clicks), REF9)
    assert (result[0] if result else None) is expected
    if result:
        assert result[1] == pytest.approx(0.91**clicks)


def test_zero_order_judgement_needs_a_reference_and_zero_orders() -> None:
    assert judge_zero_orders(totals(100), None) is None
    assert judge_zero_orders(totals(100, orders=1), REF9) is None  # converting: not waste
    assert MIN_CLICKS_FOR_FINDING == 10


def test_a_lower_reference_rate_demands_more_clicks_before_calling_waste() -> None:
    low = Reference(0.03, D(3), "campaign")
    assert judge_zero_orders(totals(32), low) is None  # at 3% CVR, 32 clicks is unremarkable
    result = judge_zero_orders(totals(100), low)
    assert result is not None
    assert result[0] is FindingLevel.CONFIRMED


def test_normalize() -> None:
    assert normalize("  Wireless   EARBUDS ") == "wireless earbuds"


# ---- end to end on the default dataset -----------------------------------------------------
@pytest.fixture(scope="module")
def data() -> GeneratedData:
    return generate(MockConfig(end_date=END, now=NOW))


@pytest.fixture(scope="module")
def reader(data: GeneratedData) -> FakeReader:
    return FakeReader(data, ORG, NOW)


@pytest.fixture(scope="module")
def analysis(reader: FakeReader) -> KeywordAnalysis:
    return asyncio.run(keyword_analysis(reader, FULL))


async def test_uses_mature_days_and_reports_provenance(analysis: KeywordAnalysis) -> None:
    assert analysis.truncated_days == 3
    assert analysis.period.end == END - timedelta(days=3)
    assert analysis.provenance.as_of == NOW
    assert analysis.low_data_targets > 0
    assert analysis.low_data_terms > 0
    assert analysis.unjudged_campaigns == 0


async def test_wasted_targets_obey_the_rule(analysis: KeywordAnalysis) -> None:
    assert analysis.wasted_targets
    seen_watch = False
    for w in analysis.wasted_targets:
        assert w.totals.orders == 0
        assert w.totals.clicks >= MIN_CLICKS_FOR_FINDING
        assert w.target.state == "enabled"
        assert not w.target.is_negative
        chance = chance_of_at_most(0, w.totals.clicks, w.reference.cvr) * 100
        assert w.chance_of_zero_orders_pct == pytest.approx(chance)
        assert (w.level is FindingLevel.CONFIRMED) == (chance <= 5)
        assert chance <= 20
        assert w.projected_monthly_spend_paise == w.totals.spend_paise * 30 // analysis.period.days
        seen_watch = seen_watch or w.level is FindingLevel.WATCH
        assert not (seen_watch and w.level is FindingLevel.CONFIRMED)  # confirmed listed first
    confirmed = [w for w in analysis.wasted_targets if w.level is FindingLevel.CONFIRMED]
    assert [w.totals.spend_paise for w in confirmed] == sorted(
        (w.totals.spend_paise for w in confirmed), reverse=True
    )


async def test_planted_wasted_keywords_with_real_traffic_are_found(
    analysis: KeywordAnalysis, data: GeneratedData
) -> None:
    found = {w.target.external_id for w in analysis.wasted_targets}
    planted = data.truth.wasted_target_ids
    assert len(found & planted) / len(planted) > 0.8  # sanity on the default seed (not scored)


async def test_negative_candidates_are_terms_not_keywords(
    analysis: KeywordAnalysis, reader: FakeReader
) -> None:
    keyword_texts = {normalize(t.text) for t in await reader.targets() if not t.is_negative}
    flagged_targets = {w.target.id for w in analysis.wasted_targets}
    assert analysis.negative_candidates
    for n in analysis.negative_candidates:
        assert n.totals.orders == 0
        assert normalize(n.term.term) not in keyword_texts  # a keyword itself: act on the keyword
        assert n.term.target_id not in flagged_targets  # parent already flagged: one fix, not two


async def test_opportunities_are_profitable_converting_and_ranked_by_evidence(
    analysis: KeywordAnalysis, reader: FakeReader
) -> None:
    exact = {normalize(t.text) for t in await reader.targets() if t.match_type == "exact"}
    assert analysis.opportunities
    bounds = [o.cvr_lower_bound_pct for o in analysis.opportunities]
    assert bounds == sorted(bounds, reverse=True)
    for o in analysis.opportunities:
        assert o.totals.orders >= OPPORTUNITY_MIN_ORDERS
        acos = acos_pct(o.totals.spend_paise, o.totals.sales_paise)
        assert acos is not None
        assert acos <= analysis.target_acos_pct
        assert normalize(o.term.term) not in exact
        lb = exact_lower_bound(o.totals.orders, o.totals.clicks)
        assert o.cvr_lower_bound_pct == pytest.approx(lb * 100)
        assert (o.strength is Strength.STRONG) == (lb >= o.reference.cvr)
        assert o.cvr_lower_bound_pct / 100 < o.totals.orders / o.totals.clicks  # below observed


async def test_underperformers_have_orders_but_too_few_and_poor_acos(
    analysis: KeywordAnalysis,
) -> None:
    wasted_ids = {w.target.id for w in analysis.wasted_targets}
    for u in analysis.underperforming_targets:
        assert u.totals.orders > 0
        assert u.target.id not in wasted_ids
        assert u.chance_pct <= 5
        assert u.kpis.acos_pct is not None
        assert u.kpis.acos_pct > analysis.target_acos_pct


async def test_stricter_target_acos_has_fewer_opportunities_and_more_underperformers(
    reader: FakeReader,
) -> None:
    strict = await keyword_analysis(reader, FULL, target_acos_pct=D(10))
    normal = await keyword_analysis(reader, FULL, target_acos_pct=D(30))
    lax = await keyword_analysis(reader, FULL, target_acos_pct=D(100))

    def opp(a: KeywordAnalysis) -> set[str]:
        return {o.term.external_id for o in a.opportunities}

    def under(a: KeywordAnalysis) -> set[str]:
        return {u.target.external_id for u in a.underperforming_targets}

    assert opp(strict) <= opp(normal) <= opp(lax)
    assert under(lax) <= under(normal) <= under(strict)


async def test_existing_exact_keyword_removes_an_opportunity(
    analysis: KeywordAnalysis, data: GeneratedData
) -> None:
    top = analysis.opportunities[0]

    class WithExact(FakeReader):
        async def targets(self) -> list[TargetInfo]:
            return [
                *await super().targets(),
                TargetInfo(
                    uuid4(),
                    "X-1",
                    uuid4(),
                    top.term.campaign_id,
                    top.term.term,
                    "exact",
                    100,
                    "enabled",
                    False,
                ),
            ]

    after = await keyword_analysis(WithExact(data, ORG, NOW), FULL)
    assert top.term.external_id not in {o.term.external_id for o in after.opportunities}
    assert len(after.opportunities) == len(analysis.opportunities) - 1


async def test_already_negated_term_is_not_proposed_again(
    analysis: KeywordAnalysis, data: GeneratedData
) -> None:
    top = analysis.negative_candidates[0]

    class WithNegative(FakeReader):
        async def targets(self) -> list[TargetInfo]:
            return [
                *await super().targets(),
                TargetInfo(
                    uuid4(),
                    "X-2",
                    uuid4(),
                    top.term.campaign_id,
                    top.term.term,
                    "exact",
                    0,
                    "enabled",
                    True,
                ),
            ]

    after = await keyword_analysis(WithNegative(data, ORG, NOW), FULL)
    assert top.term.external_id not in {n.term.external_id for n in after.negative_candidates}


async def test_input_validation_and_empty_account(reader: FakeReader, data: GeneratedData) -> None:
    with pytest.raises(ValueError, match="positive"):
        await keyword_analysis(reader, FULL, target_acos_pct=D(0))

    class Empty(FakeReader):
        async def freshness(self) -> Freshness:
            return Freshness(None, None)

    with pytest.raises(NoDataError):
        await keyword_analysis(Empty(data, ORG, NOW), FULL)
