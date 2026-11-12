"""Recommendation rules, approval workflow and generation (unit level, in-memory store)."""

import dataclasses
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.application.optimization.generate import (
    generate_recommendations,
    propose_bid_change,
)
from app.application.recommendations.service import Actor, RecommendationService
from app.domain.kpi.metrics import MetricTotals
from app.domain.kpi.periods import DateRange
from app.domain.recommendations import (
    APPROVAL_TTL,
    MAX_BID_CHANGE_PCT,
    RECOMMENDATION_TTL,
    Decision,
    ExpiredError,
    HashMismatchError,
    InvalidTransitionError,
    NotFoundError,
    Recommendation,
    RecType,
    Status,
    content_hash,
    guardrail_violations,
    next_status,
)
from app.infrastructure.providers.mock.generator import MockConfig, generate
from tests.unit.fake_reader import FakeReader
from tests.unit.memory_recommendation_store import MemoryRecommendationStore

ORG = UUID("12121212-1212-4212-8212-121212121212")
OTHER_ORG = UUID("34343434-3434-4434-8434-343434343434")
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
END = date(2026, 10, 31)
ADMIN = Actor("admin-1", "req-1")
OTHER_ADMIN = Actor("admin-2", "req-2")


def rec(**kw: Any) -> Recommendation:
    base: dict[str, Any] = {
        "id": UUID(int=1),
        "organization_id": ORG,
        "type": RecType.BID_CHANGE,
        "entity_type": "target",
        "entity_id": "t-1",
        "entity_label": "yoga mat",
        "current_value": 1000,
        "proposed_value": 900,
        "proposed_text": None,
        "rationale": "r",
        "evidence": {"clicks": 50, "orders": 5},
        "confidence": "medium",
        "expected_impact": "direction only",
        "data_source": "MOCK",
        "created_at": NOW,
        "expires_at": NOW + RECOMMENDATION_TTL,
    }
    return Recommendation(**(base | kw))


async def service_with(
    *recs: Recommendation,
) -> tuple[RecommendationService, MemoryRecommendationStore]:
    store = MemoryRecommendationStore(ORG)
    await store.add_new(recs)
    return RecommendationService(store, ORG), store


# ---- content hash --------------------------------------------------------------------------
def test_hash_is_deterministic_and_covers_content_but_not_status_or_id() -> None:
    a = rec()
    assert a.content_hash == rec().content_hash == content_hash(a)
    assert dataclasses.replace(a, status=Status.APPROVED).content_hash == a.content_hash
    assert rec(id=UUID(int=2)).content_hash == a.content_hash
    for change in (
        {"proposed_value": 901},
        {"current_value": 999},
        {"entity_id": "t-2"},
        {"evidence": {"clicks": 51, "orders": 5}},
        {"organization_id": OTHER_ORG},
        {"type": RecType.BUDGET_CHANGE},
    ):
        assert rec(**change).content_hash != a.content_hash, change


# ---- guardrails ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kw", "fragment"),
    [
        ({"proposed_value": 700}, "exceeds"),  # -30%
        ({"proposed_value": 1300}, "exceeds"),
        ({"proposed_value": 1000}, "equals"),
        ({"proposed_value": 0}, "positive"),
        ({"current_value": None}, "positive"),
        ({"expires_at": NOW}, "expiry"),
        ({"data_source": "REAL"}, "data source"),
    ],
)
def test_guardrails_reject_unsafe_proposals(kw: dict[str, Any], fragment: str) -> None:
    assert any(fragment in p for p in guardrail_violations(rec(**kw)))


def test_guardrails_accept_boundary_changes_and_valid_negatives() -> None:
    assert guardrail_violations(rec(proposed_value=800)) == []
    assert guardrail_violations(rec(proposed_value=1200)) == []
    neg = rec(
        type=RecType.NEGATIVE_KEYWORD, current_value=None, proposed_value=None, proposed_text="free"
    )
    assert guardrail_violations(neg) == []
    assert guardrail_violations(dataclasses.replace(neg, proposed_text="")) != []


@given(
    bid=st.integers(10, 50_000),
    clicks=st.integers(30, 5000),
    orders=st.integers(3, 500),
    spend=st.integers(1000, 10**8),
    sales=st.integers(1000, 10**8),
)
def test_bid_proposals_always_respect_the_cap(
    bid: int, clicks: int, orders: int, spend: int, sales: int
) -> None:
    r = propose_bid_change(
        org=ORG,
        target_id="t",
        label="x",
        bid_paise=bid,
        totals=MetricTotals(clicks=clicks, orders=orders, spend_paise=spend, sales_paise=sales),
        target_acos_pct=Decimal(30),
        period=DateRange(END - timedelta(days=27), END),
        data_source="MOCK",
        now=NOW,
    )
    if r is not None:
        assert guardrail_violations(r) == []
        assert abs(r.proposed_value - bid) * 100 <= bid * MAX_BID_CHANGE_PCT  # type: ignore[operator]


def test_bid_needs_enough_data() -> None:
    kw: dict[str, Any] = {
        "org": ORG,
        "target_id": "t",
        "label": "x",
        "bid_paise": 1000,
        "target_acos_pct": Decimal(30),
        "period": DateRange(END - timedelta(days=27), END),
        "data_source": "MOCK",
        "now": NOW,
    }
    good = MetricTotals(clicks=100, orders=10, spend_paise=50_000, sales_paise=100_000)  # ACOS 50%
    assert propose_bid_change(totals=good, **kw) is not None
    assert propose_bid_change(totals=dataclasses.replace(good, clicks=29), **kw) is None
    assert propose_bid_change(totals=dataclasses.replace(good, orders=2), **kw) is None
    on_target = MetricTotals(clicks=100, orders=10, spend_paise=30_000, sales_paise=100_000)
    assert propose_bid_change(totals=on_target, **kw) is None  # change below the noise floor
    lower = propose_bid_change(totals=good, **kw)
    assert lower is not None and lower.proposed_value == 800  # 50% ACOS -> clamp to -20%


# ---- lifecycle -----------------------------------------------------------------------------
def test_state_machine_only_leaves_pending() -> None:
    r = rec()
    assert next_status(r, Decision.APPROVE, NOW) is Status.APPROVED
    assert next_status(r, Decision.REJECT, NOW) is Status.REJECTED
    for s in (Status.APPROVED, Status.REJECTED, Status.EXPIRED):
        with pytest.raises(InvalidTransitionError):
            next_status(dataclasses.replace(r, status=s), Decision.APPROVE, NOW)
    with pytest.raises(ExpiredError):
        next_status(r, Decision.APPROVE, r.expires_at)


async def test_approve_writes_decision_status_and_audit_together() -> None:
    r = rec()
    svc, store = await service_with(r)
    res = await svc.decide(
        r.id, Decision.APPROVE, ADMIN, approved_hash=r.content_hash, comment="ok", now=NOW
    )
    assert res.recommendation.status is Status.APPROVED and not res.replayed
    assert res.approval.decided_by == "admin-1" and res.approval.content_hash == r.content_hash
    assert (await store.get(r.id)).status is Status.APPROVED  # type: ignore[union-attr]
    assert [a.action for a in store.audit] == ["recommendation.approved"]
    entry = store.audit[0]
    assert entry.actor == "admin-1" and entry.request_id == "req-1"
    assert entry.before == {"status": "pending"} and entry.after["status"] == "approved"  # type: ignore[index]


async def test_reject_is_recorded_and_cannot_later_be_approved() -> None:
    r = rec()
    svc, _ = await service_with(r)
    await svc.decide(
        r.id, Decision.REJECT, ADMIN, approved_hash=r.content_hash, comment=None, now=NOW
    )
    with pytest.raises(InvalidTransitionError):
        await svc.decide(
            r.id, Decision.APPROVE, ADMIN, approved_hash=r.content_hash, comment=None, now=NOW
        )


async def test_wrong_hash_is_refused_and_changes_nothing() -> None:
    r = rec()
    svc, store = await service_with(r)
    with pytest.raises(HashMismatchError):
        await svc.decide(
            r.id, Decision.APPROVE, ADMIN, approved_hash="0" * 64, comment=None, now=NOW
        )
    assert (await store.get(r.id)).status is Status.PENDING  # type: ignore[union-attr]
    assert store.audit == [] and store.approvals == {}


async def test_content_tampered_after_creation_cannot_be_approved() -> None:
    r = rec()
    svc, store = await service_with(r)
    # Someone edits the stored proposal but keeps the old hash on the row.
    store.recs[r.id] = dataclasses.replace(r, proposed_value=800)
    with pytest.raises(HashMismatchError):
        await svc.decide(
            r.id, Decision.APPROVE, ADMIN, approved_hash=r.content_hash, comment=None, now=NOW
        )


async def test_expired_recommendation_cannot_be_approved_and_expiry_is_recorded() -> None:
    r = rec()
    svc, store = await service_with(r)
    late = r.expires_at + timedelta(seconds=1)
    with pytest.raises(ExpiredError):
        await svc.decide(
            r.id, Decision.APPROVE, ADMIN, approved_hash=r.content_hash, comment=None, now=late
        )
    assert (await store.get(r.id)).status is Status.EXPIRED  # type: ignore[union-attr]
    assert [a.action for a in store.audit] == ["recommendation.expired"]
    with pytest.raises(InvalidTransitionError):  # and stays expired
        await svc.decide(
            r.id, Decision.APPROVE, ADMIN, approved_hash=r.content_hash, comment=None, now=NOW
        )


async def test_repeating_the_identical_decision_is_idempotent_but_others_conflict() -> None:
    r = rec()
    svc, store = await service_with(r)
    first = await svc.decide(
        r.id, Decision.APPROVE, ADMIN, approved_hash=r.content_hash, comment=None, now=NOW
    )
    again = await svc.decide(
        r.id,
        Decision.APPROVE,
        ADMIN,
        approved_hash=r.content_hash,
        comment=None,
        now=NOW + timedelta(minutes=1),
    )
    assert again.replayed and again.approval.id == first.approval.id
    assert len(store.approvals) == 1 and len(store.audit) == 1  # nothing written twice
    with pytest.raises(InvalidTransitionError):  # a different person cannot re-decide
        await svc.decide(
            r.id, Decision.APPROVE, OTHER_ADMIN, approved_hash=r.content_hash, comment=None, now=NOW
        )
    with pytest.raises(InvalidTransitionError):  # nor reverse it
        await svc.decide(
            r.id, Decision.REJECT, ADMIN, approved_hash=r.content_hash, comment=None, now=NOW
        )


async def test_unknown_recommendation_is_not_found() -> None:
    svc, _ = await service_with()
    with pytest.raises(NotFoundError):
        await svc.decide(
            UUID(int=99), Decision.APPROVE, ADMIN, approved_hash="x", comment=None, now=NOW
        )


async def test_store_is_tenant_bound_so_another_org_cannot_add_or_see() -> None:
    store = MemoryRecommendationStore(ORG)
    with pytest.raises(ValueError, match="another organization"):
        await store.add_new([rec(organization_id=OTHER_ORG)])


# ---- execution gate ------------------------------------------------------------------------
async def approved(svc: RecommendationService, r: Recommendation) -> None:
    await svc.decide(
        r.id, Decision.APPROVE, ADMIN, approved_hash=r.content_hash, comment=None, now=NOW
    )


async def test_execution_is_blocked_while_disabled_even_if_approved() -> None:
    r = rec()
    svc, store = await service_with(r)
    await approved(svc, r)
    a = await svc.request_execution(r.id, ADMIN, execution_enabled=False, now=NOW)
    assert (a.status, a.reason, a.dry_run) == ("blocked", "execution_disabled", True)
    assert [x.action for x in store.audit][-1] == "execution.blocked"
    assert len(await store.executions()) == 1


async def test_execution_is_blocked_unless_approved() -> None:
    r = rec()
    svc, _ = await service_with(r)
    a = await svc.request_execution(r.id, ADMIN, execution_enabled=True, now=NOW)
    assert a.reason == "not_approved" and a.approval_id is None


async def test_rejected_recommendation_never_executes() -> None:
    r = rec()
    svc, _ = await service_with(r)
    await svc.decide(
        r.id, Decision.REJECT, ADMIN, approved_hash=r.content_hash, comment=None, now=NOW
    )
    assert (await svc.request_execution(r.id, ADMIN, execution_enabled=True, now=NOW)).reason == (
        "not_approved"
    )


async def test_execution_checks_approval_age_and_content_hash_and_still_never_sends() -> None:
    r = rec()
    svc, store = await service_with(r)
    await approved(svc, r)
    ok = await svc.request_execution(r.id, ADMIN, execution_enabled=True, now=NOW)
    assert (
        ok.reason == "no_execution_provider" and ok.status == "blocked"
    )  # nothing can be sent yet
    old = await svc.request_execution(
        r.id, ADMIN, execution_enabled=True, now=NOW + APPROVAL_TTL + timedelta(seconds=1)
    )
    assert old.reason == "approval_expired"
    store.recs[r.id] = dataclasses.replace(store.recs[r.id], proposed_value=800)
    changed = await svc.request_execution(r.id, ADMIN, execution_enabled=True, now=NOW)
    assert changed.reason == "content_changed_since_approval"
    assert all(a.status == "blocked" for a in store.attempts)


async def test_execution_of_unknown_recommendation_is_not_found() -> None:
    svc, _ = await service_with()
    with pytest.raises(NotFoundError):
        await svc.request_execution(UUID(int=5), ADMIN, execution_enabled=False, now=NOW)


# ---- generation ----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def reader() -> FakeReader:
    data = generate(MockConfig(end_date=END, now=NOW))
    return FakeReader(data, ORG, NOW)


async def test_generation_is_deterministic_guardrailed_and_traceable(reader: FakeReader) -> None:
    period = DateRange(END - timedelta(days=89), END)
    a = await generate_recommendations(reader, ORG, period, NOW)
    b = await generate_recommendations(reader, ORG, period, NOW)
    assert [(r.id, r.content_hash) for r in a.recommendations] == [
        (r.id, r.content_hash) for r in b.recommendations
    ]
    assert a.recommendations, "the mock data plants waste, so proposals are expected"
    types = {r.type for r in a.recommendations}
    assert RecType.NEGATIVE_KEYWORD in types
    for r in a.recommendations:
        assert guardrail_violations(r) == []  # guardrail violations = 0
        assert r.status is Status.PENDING and r.data_source == "MOCK"
        assert r.organization_id == ORG
        assert r.evidence["rule"] and r.evidence["period"] and r.rationale
        assert r.expires_at == NOW + RECOMMENDATION_TTL
        assert r.content_hash == content_hash(r)
    assert len({r.id for r in a.recommendations}) == len(a.recommendations)


async def test_generating_twice_stores_each_proposal_once(reader: FakeReader) -> None:
    period = DateRange(END - timedelta(days=89), END)
    svc, store = await service_with()
    recs = (await generate_recommendations(reader, ORG, period, NOW)).recommendations
    assert await svc.add_generated(recs, ADMIN, NOW) == len(recs)
    assert await svc.add_generated(recs, ADMIN, NOW) == 0
    assert len(store.recs) == len(recs)
    assert [a.action for a in store.audit].count("recommendation.created") == len(recs)


async def test_negative_proposals_come_only_from_confirmed_findings(reader: FakeReader) -> None:
    from app.application.analytics.keywords import FindingLevel, keyword_analysis

    period = DateRange(END - timedelta(days=89), END)
    analysis = await keyword_analysis(reader, period)
    confirmed = {
        str(n.term.id) for n in analysis.negative_candidates if n.level is FindingLevel.CONFIRMED
    }
    got = {
        r.entity_id
        for r in (await generate_recommendations(reader, ORG, period, NOW)).recommendations
        if r.type is RecType.NEGATIVE_KEYWORD
    }
    assert got == confirmed
