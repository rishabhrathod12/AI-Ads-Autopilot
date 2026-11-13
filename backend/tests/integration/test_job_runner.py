"""The scheduled-job runner on a real PostgreSQL: org listing under RLS, isolation, locking.

Skipped unless TEST_DATABASE_URL is set. Migration 0008 adds the policy the org listing uses.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.jobs.maintenance import SYSTEM_ACTOR
from app.application.sync_service import SyncService
from app.domain.recommendations import RECOMMENDATION_TTL, Recommendation, RecType, Status
from app.domain.tenant import TenantContext
from app.infrastructure.db.ads_store import SqlAdsStore
from app.infrastructure.db.forecast_store import SqlForecastStore
from app.infrastructure.db.job_runner import _lock_key, list_organization_ids, run_jobs
from app.infrastructure.db.models import AuditLog, Organization
from app.infrastructure.db.recommendation_store import SqlRecommendationStore
from app.infrastructure.db.session import maintenance_session, tenant_session
from app.infrastructure.providers.mock.generator import MockConfig
from app.infrastructure.providers.mock.provider import MockAdvertisingDataProvider

pytestmark = pytest.mark.db
ROLE = "adpilot_app"
END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
CFG = MockConfig(
    days=90, end_date=END, n_campaigns=6, ad_groups_per_campaign=1, keywords_per_ad_group=5, now=NOW
)
PROVIDER = MockAdvertisingDataProvider(CFG)


async def _org(engine: AsyncEngine, *, with_data: bool = False) -> uuid.UUID:
    org = uuid.uuid4()
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        s.add(Organization(id=org, name="Jobs Test"))
        await s.flush()
        if with_data:
            start = END - timedelta(days=CFG.days - 1)
            await SyncService(PROVIDER, SqlAdsStore(s, org)).sync(start, END)
    return org


def _pending(org: uuid.UUID, created: datetime) -> Recommendation:
    return Recommendation(
        id=uuid.uuid4(),
        organization_id=org,
        type=RecType.BID_CHANGE,
        entity_type="target",
        entity_id=str(uuid.uuid4()),
        entity_label="yoga mat",
        current_value=1000,
        proposed_value=900,
        proposed_text=None,
        rationale="r",
        evidence={"rule": "x", "n": str(uuid.uuid4())},
        confidence="medium",
        expected_impact="direction only",
        data_source="MOCK",
        created_at=created,
        expires_at=created + RECOMMENDATION_TTL,
    )


async def _add(engine: AsyncEngine, org: uuid.UUID, *recs: Recommendation) -> None:
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        await SqlRecommendationStore(s, org).add_new(list(recs))


async def _status(engine: AsyncEngine, org: uuid.UUID, rec: Recommendation) -> Status:
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        got = await SqlRecommendationStore(s, org).get(rec.id)
    assert got is not None
    return got.status


async def test_all_organizations_are_listed_for_the_runner_but_for_nobody_else(
    engine: AsyncEngine,
) -> None:
    a, b = await _org(engine), await _org(engine)
    listed = await list_organization_ids(engine, ROLE)
    assert {a, b} <= set(listed)
    # An ordinary session with no tenant (and not a maintenance session) sees no organizations.
    async with tenant_session(engine, TenantContext(uuid.UUID(int=0)), ROLE) as s:
        assert (await s.execute(select(Organization.id))).all() == []
    # A tenant session still sees only its own organization.
    async with tenant_session(engine, TenantContext(a), ROLE) as s:
        assert [r[0] for r in (await s.execute(select(Organization.id))).all()] == [a]


async def test_the_maintenance_setting_opens_only_the_organization_list(
    engine: AsyncEngine,
) -> None:
    org = await _org(engine)
    await _add(engine, org, _pending(org, NOW - timedelta(days=1)))
    async with maintenance_session(engine, ROLE) as s:
        assert (await s.execute(text("SELECT 1 FROM organizations"))).all() != []
        for table in ("recommendations", "audit_logs", "memberships", "performance_daily"):
            assert (await s.execute(text(f"SELECT 1 FROM {table}"))).all() == [], table  # noqa: S608


async def test_expiry_runs_per_organization_and_stays_inside_each(engine: AsyncEngine) -> None:
    a, b = await _org(engine), await _org(engine)
    old_a, fresh_a = _pending(a, NOW - timedelta(days=9)), _pending(a, NOW - timedelta(days=1))
    old_b = _pending(b, NOW - timedelta(days=9))
    await _add(engine, a, old_a, fresh_a)
    await _add(engine, b, old_b)
    outcomes = await run_jobs(engine, ROLE, ["expire"], NOW, org_ids=[a])  # only org a
    assert [(o.organization_id, o.status) for o in outcomes] == [(a, "done")]
    assert await _status(engine, a, old_a) is Status.EXPIRED
    assert await _status(engine, a, fresh_a) is Status.PENDING
    assert await _status(engine, b, old_b) is Status.PENDING  # org b was not asked
    async with tenant_session(engine, TenantContext(a), ROLE) as s:
        entries = (
            await s.scalars(select(AuditLog).where(AuditLog.action == "recommendation.expired"))
        ).all()
    assert [(e.actor, e.entity_id) for e in entries] == [(SYSTEM_ACTOR, str(old_a.id))]


async def test_running_for_everyone_covers_new_organizations_and_is_idempotent(
    engine: AsyncEngine,
) -> None:
    org = await _org(engine)
    old = _pending(org, NOW - timedelta(days=9))
    await _add(engine, org, old)
    first = await run_jobs(engine, ROLE, ["expire"], NOW)
    mine = [o for o in first if o.organization_id == org]
    assert [(o.status, o.detail) for o in mine] == [("done", "1 recommendation(s) expired")]
    second = [o for o in await run_jobs(engine, ROLE, ["expire"], NOW) if o.organization_id == org]
    assert [o.detail for o in second] == ["0 recommendation(s) expired"]
    assert await _status(engine, org, old) is Status.EXPIRED


async def test_forecast_job_stores_audits_and_then_skips_until_old(engine: AsyncEngine) -> None:
    org = await _org(engine, with_data=True)
    (first,) = await run_jobs(engine, ROLE, ["forecast"], NOW, org_ids=[org])
    assert (first.status, first.detail) == ("done", "stored a new 14-day forecast")
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        store = SqlForecastStore(s, org)
        spend, sales = (
            await store.latest("account", None, "spend"),
            await store.latest("account", None, "sales"),
        )
        audited = (
            await s.scalars(select(AuditLog.actor).where(AuditLog.action == "forecast.created"))
        ).all()
    assert spend is not None and sales is not None and list(audited) == [SYSTEM_ACTOR]
    # Stored rows are stamped with the database clock, so "recent" is judged against it.
    (second,) = await run_jobs(engine, ROLE, ["forecast"], datetime.now(UTC), org_ids=[org])
    assert second.status == "skipped" and "recent forecast" in second.detail


async def test_an_organization_without_data_is_skipped_not_failed(engine: AsyncEngine) -> None:
    org = await _org(engine)
    (outcome,) = await run_jobs(engine, ROLE, ["forecast"], NOW, org_ids=[org])
    assert outcome.status == "skipped" and "NoDataError" in outcome.detail


async def test_one_failing_organization_does_not_stop_the_rest(engine: AsyncEngine) -> None:
    good = await _org(engine)
    old = _pending(good, NOW - timedelta(days=9))
    await _add(engine, good, old)
    missing = uuid.uuid4()  # an org id that does not exist: its job runs but finds nothing
    outcomes = await run_jobs(engine, ROLE, ["expire"], NOW, org_ids=[missing, good])
    assert [o.organization_id for o in outcomes] == [missing, good]
    assert outcomes[1].status == "done"
    assert await _status(engine, good, old) is Status.EXPIRED


async def test_an_advisory_lock_makes_an_overlapping_run_skip(engine: AsyncEngine) -> None:
    org = await _org(engine)
    old = _pending(org, NOW - timedelta(days=9))
    await _add(engine, org, old)
    async with engine.connect() as holder:  # another runner holding this job's lock
        await holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _lock_key(org, "expire")})
        (outcome,) = await run_jobs(engine, ROLE, ["expire"], NOW, org_ids=[org])
        await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _lock_key(org, "expire")})
    assert (outcome.status, "lock" in outcome.detail) == ("skipped", True)
    assert await _status(engine, org, old) is Status.PENDING  # did no work while locked
    (after,) = await run_jobs(engine, ROLE, ["expire"], NOW, org_ids=[org])
    assert after.status == "done"


async def test_unknown_job_names_are_refused(engine: AsyncEngine) -> None:
    with pytest.raises(ValueError, match="unknown job"):
        await run_jobs(engine, ROLE, ["delete-everything"], NOW, org_ids=[])
