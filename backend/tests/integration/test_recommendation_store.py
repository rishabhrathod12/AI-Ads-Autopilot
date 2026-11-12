"""SQL recommendation store against a real PostgreSQL: isolation, immutability, atomic decisions.

UNVERIFIED in the environment where this was written (no test database was available): skipped
there. Run with TEST_DATABASE_URL set before relying on migration 0006 or the SQL store.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.recommendations.service import Actor, RecommendationService
from app.domain.recommendations import (
    ApprovalRecord,
    Decision,
    ExpiredError,
    Recommendation,
    RecType,
    Status,
)
from app.domain.tenant import TenantContext
from app.infrastructure.db.models import AuditLog, Organization
from app.infrastructure.db.recommendation_models import ApprovalRow, RecommendationRow
from app.infrastructure.db.recommendation_store import SqlRecommendationStore
from app.infrastructure.db.session import tenant_session

pytestmark = pytest.mark.db
ROLE = "adpilot_app"
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)


def make(org: uuid.UUID, proposed: int = 900, **kw: object) -> Recommendation:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "organization_id": org,
        "type": RecType.BID_CHANGE,
        "entity_type": "target",
        "entity_id": str(uuid.uuid4()),
        "entity_label": "yoga mat",
        "current_value": 1000,
        "proposed_value": proposed,
        "proposed_text": None,
        "rationale": "r",
        "evidence": {"rule": "x", "clicks": 50},
        "confidence": "medium",
        "expected_impact": "direction only",
        "data_source": "MOCK",
        "created_at": NOW,
        "expires_at": NOW + timedelta(days=7),
    }
    return Recommendation(**(base | kw))  # type: ignore[arg-type]


async def new_org(engine: AsyncEngine) -> uuid.UUID:
    org = uuid.uuid4()
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        s.add(Organization(id=org, name="O"))
    return org


async def test_add_new_is_idempotent_and_round_trips(engine: AsyncEngine) -> None:
    org = await new_org(engine)
    r = make(org)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        store = SqlRecommendationStore(s, org)
        assert [x.id for x in await store.add_new([r])] == [r.id]
        assert await store.add_new([r]) == []  # same content hash: nothing inserted
        got = await store.get(r.id)
    assert got is not None and got.content_hash == r.content_hash and got.evidence == r.evidence
    assert got.status is Status.PENDING


async def test_tenants_cannot_see_or_decide_each_others_recommendations(
    engine: AsyncEngine,
) -> None:
    a, b = await new_org(engine), await new_org(engine)
    ra = make(a)
    async with tenant_session(engine, TenantContext(a), ROLE) as s:
        await SqlRecommendationStore(s, a).add_new([ra])
    async with tenant_session(engine, TenantContext(b), ROLE) as s:
        other = SqlRecommendationStore(s, b)
        assert await other.get(ra.id) is None
        assert await other.list_recommendations() == []
        assert not await other.transition(ra.id, expected=Status.PENDING, new=Status.APPROVED)
        with pytest.raises(ValueError, match="another organization"):
            await other.add_new([ra])
    async with tenant_session(engine, TenantContext(a), ROLE) as s:
        assert (await SqlRecommendationStore(s, a).get(ra.id)).status is Status.PENDING  # type: ignore[union-attr]


async def test_transition_is_atomic_compare_and_set(engine: AsyncEngine) -> None:
    org = await new_org(engine)
    r = make(org)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        store = SqlRecommendationStore(s, org)
        await store.add_new([r])
        assert await store.transition(r.id, expected=Status.PENDING, new=Status.APPROVED)
        assert not await store.transition(r.id, expected=Status.PENDING, new=Status.REJECTED)
        assert (await store.get(r.id)).status is Status.APPROVED  # type: ignore[union-attr]


async def test_one_decision_per_recommendation_enforced_by_the_database(
    engine: AsyncEngine,
) -> None:
    org = await new_org(engine)
    r = make(org)

    def approval() -> ApprovalRecord:
        return ApprovalRecord(
            uuid.uuid4(), org, r.id, "admin", Decision.APPROVE, None, r.content_hash, NOW
        )

    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        store = SqlRecommendationStore(s, org)
        await store.add_new([r])
        await store.add_approval(approval())
    with pytest.raises(IntegrityError):
        async with tenant_session(engine, TenantContext(org), ROLE) as s:
            await SqlRecommendationStore(s, org).add_approval(approval())


async def test_approvals_executions_audit_and_proposal_content_are_immutable(
    engine: AsyncEngine,
) -> None:
    org = await new_org(engine)
    r = make(org)
    actor = Actor("admin", "req")
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        store = SqlRecommendationStore(s, org)
        svc = RecommendationService(store, org)
        await svc.add_generated([r], actor, NOW)
        await svc.decide(
            r.id, Decision.APPROVE, actor, approved_hash=r.content_hash, comment=None, now=NOW
        )
        await svc.request_execution(r.id, actor, execution_enabled=False, now=NOW)
    attempts: list[tuple[type, object]] = [
        (ApprovalRow, update(ApprovalRow).values(decided_by="x")),
        (ApprovalRow, delete(ApprovalRow)),
        (AuditLog, update(AuditLog).values(actor="x")),
        (AuditLog, delete(AuditLog)),
        (RecommendationRow, update(RecommendationRow).values(proposed_value=1)),
        (RecommendationRow, update(RecommendationRow).values(content_hash="0" * 64)),
        (RecommendationRow, delete(RecommendationRow)),
    ]
    for _model, stmt in attempts:
        with pytest.raises((ProgrammingError, DBAPIError)):
            async with tenant_session(engine, TenantContext(org), ROLE) as s:
                await s.execute(stmt)  # type: ignore[call-overload]
    with pytest.raises((ProgrammingError, DBAPIError)):
        async with tenant_session(engine, TenantContext(org), ROLE) as s:
            await s.execute(text("DELETE FROM execution_attempts"))


async def test_the_full_flow_persists_decision_status_and_audit_together(
    engine: AsyncEngine,
) -> None:
    org = await new_org(engine)
    r = make(org)
    actor = Actor("admin", "req-9")
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        svc = RecommendationService(SqlRecommendationStore(s, org), org)
        await svc.add_generated([r], actor, NOW)
        await svc.decide(
            r.id, Decision.REJECT, actor, approved_hash=r.content_hash, comment="no", now=NOW
        )
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        store = SqlRecommendationStore(s, org)
        assert (await store.get(r.id)).status is Status.REJECTED  # type: ignore[union-attr]
        assert (await store.approval_for(r.id)).comment == "no"  # type: ignore[union-attr]
        actions = {a.action for a in await store.audit_log()}
        assert {"recommendation.created", "recommendation.rejected"} <= actions
        rows = (await s.scalars(select(AuditLog.request_id))).all()
        assert "req-9" in rows


async def test_expiry_is_recorded_when_the_caller_commits(engine: AsyncEngine) -> None:
    """The service raises after writing; the API returns (not raises) so the expiry commits."""
    org = await new_org(engine)
    r = make(org)
    actor = Actor("admin")
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        await SqlRecommendationStore(s, org).add_new([r])
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        svc = RecommendationService(SqlRecommendationStore(s, org), org)
        with pytest.raises(ExpiredError):
            await svc.decide(
                r.id,
                Decision.APPROVE,
                actor,
                approved_hash=r.content_hash,
                comment=None,
                now=r.expires_at + timedelta(seconds=1),
            )
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        assert (await SqlRecommendationStore(s, org).get(r.id)).status is Status.EXPIRED  # type: ignore[union-attr]
