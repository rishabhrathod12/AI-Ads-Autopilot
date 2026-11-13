"""Scheduled maintenance jobs: expiry and forecast refresh, over in-memory stores."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from app.application.jobs.maintenance import (
    FORECAST_MAX_AGE,
    SYSTEM_ACTOR,
    expire_overdue,
    refresh_forecast,
)
from app.domain.recommendations import RECOMMENDATION_TTL, Recommendation, RecType, Status
from app.infrastructure.providers.mock.generator import GeneratedData, MockConfig, generate
from tests.unit.fake_reader import FakeReader
from tests.unit.memory_audit_store import MemoryAuditStore
from tests.unit.memory_recommendation_store import MemoryRecommendationStore
from tests.unit.test_api_analytics import MemoryForecastStore

ORG = UUID("12121212-1212-4212-8212-121212121212")
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
END = date(2026, 10, 31)


def rec(n: int, created: datetime, **kw: Any) -> Recommendation:
    base: dict[str, Any] = {
        "id": UUID(int=n),
        "organization_id": ORG,
        "type": RecType.BID_CHANGE,
        "entity_type": "target",
        "entity_id": f"t-{n}",
        "entity_label": "yoga mat",
        "current_value": 1000,
        "proposed_value": 900,
        "proposed_text": None,
        "rationale": "r",
        "evidence": {"n": n},
        "confidence": "medium",
        "expected_impact": "direction only",
        "data_source": "MOCK",
        "created_at": created,
        "expires_at": created + RECOMMENDATION_TTL,
    }
    return Recommendation(**(base | kw))


@pytest.fixture(scope="module")
def data() -> GeneratedData:
    return generate(MockConfig(end_date=END, now=NOW))


# ---- expiry ---------------------------------------------------------------------------------
async def test_overdue_pending_recommendations_are_expired_and_audited() -> None:
    store = MemoryRecommendationStore(ORG)
    old, fresh = rec(1, NOW - timedelta(days=8)), rec(2, NOW - timedelta(days=1))
    await store.add_new([old, fresh])
    outcome = await expire_overdue(store, ORG, NOW, request_id="job-1")
    assert (outcome.status, outcome.detail) == ("done", "1 recommendation(s) expired")
    assert store.recs[old.id].status is Status.EXPIRED
    assert store.recs[fresh.id].status is Status.PENDING
    (entry,) = store.audit
    assert (entry.action, entry.actor, entry.entity_id) == (
        "recommendation.expired",
        SYSTEM_ACTOR,
        str(old.id),
    )
    assert entry.before == {"status": "pending"} and entry.request_id == "job-1"


async def test_expiry_is_idempotent_and_never_touches_decided_recommendations() -> None:
    store = MemoryRecommendationStore(ORG)
    approved = rec(1, NOW - timedelta(days=30), status=Status.APPROVED)
    rejected = rec(2, NOW - timedelta(days=30), status=Status.REJECTED)
    overdue = rec(3, NOW - timedelta(days=30))
    await store.add_new([approved, rejected, overdue])
    first = await expire_overdue(store, ORG, NOW)
    second = await expire_overdue(store, ORG, NOW)
    assert first.detail == "1 recommendation(s) expired"
    assert second.detail == "0 recommendation(s) expired"
    assert store.recs[approved.id].status is Status.APPROVED  # an approval is never undone
    assert store.recs[rejected.id].status is Status.REJECTED
    assert len(store.audit) == 1


async def test_expiry_boundary_is_inclusive_like_the_approval_rule() -> None:
    store = MemoryRecommendationStore(ORG)
    exactly = rec(1, NOW - RECOMMENDATION_TTL)  # expires_at == now
    just_before = rec(2, NOW - RECOMMENDATION_TTL + timedelta(seconds=1))
    await store.add_new([exactly, just_before])
    await expire_overdue(store, ORG, NOW)
    assert store.recs[exactly.id].status is Status.EXPIRED
    assert store.recs[just_before.id].status is Status.PENDING


async def test_nothing_to_expire_is_a_quiet_success() -> None:
    store = MemoryRecommendationStore(ORG)
    outcome = await expire_overdue(store, ORG, NOW)
    assert outcome.status == "done" and store.audit == []


# ---- forecast refresh -----------------------------------------------------------------------
async def test_forecast_is_stored_and_audited(data: GeneratedData) -> None:
    forecasts, audit = MemoryForecastStore(), MemoryAuditStore()
    outcome = await refresh_forecast(FakeReader(data, ORG, NOW), forecasts, audit, ORG, NOW)
    assert outcome.status == "done"
    assert {r.metric for r in forecasts.rows} == {"spend", "sales"}
    (entry,) = audit.entries
    assert (entry.action, entry.actor) == ("forecast.created", SYSTEM_ACTOR)
    assert set(entry.after or {}) == {"run_ids"}


async def test_a_recent_forecast_is_not_recomputed_unless_forced(data: GeneratedData) -> None:
    forecasts, audit = MemoryForecastStore(), MemoryAuditStore()
    reader = FakeReader(data, ORG, NOW)
    await refresh_forecast(reader, forecasts, audit, ORG, NOW)
    stored = len(forecasts.rows)
    soon = NOW + FORECAST_MAX_AGE - timedelta(minutes=1)  # stored rows are stamped NOW
    again = await refresh_forecast(reader, forecasts, audit, ORG, soon)
    assert (again.status, len(forecasts.rows)) == ("skipped", stored)
    forced = await refresh_forecast(reader, forecasts, audit, ORG, soon, force=True)
    assert (forced.status, len(forecasts.rows)) == ("done", stored * 2)
    later = await refresh_forecast(reader, forecasts, audit, ORG, NOW + FORECAST_MAX_AGE)
    assert later.status == "done"  # old enough: refreshed without force


async def test_not_enough_history_is_skipped_not_failed() -> None:
    short = generate(MockConfig(days=30, end_date=END, now=NOW, n_campaigns=6))
    forecasts, audit = MemoryForecastStore(), MemoryAuditStore()
    outcome = await refresh_forecast(FakeReader(short, ORG, NOW), forecasts, audit, ORG, NOW)
    assert outcome.status == "skipped" and "InsufficientHistoryError" in outcome.detail
    assert forecasts.rows == [] and audit.entries == []  # nothing stored, nothing audited
