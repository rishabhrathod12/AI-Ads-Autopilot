"""The assistant's recommendation tool over the real SQL store: tenant isolation under RLS.

Skipped unless TEST_DATABASE_URL is set.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.assistant.evidence import Evidence
from app.application.assistant.tools import ToolContext, ToolRunner
from app.application.ports.llm import ToolCall
from app.domain.recommendations import RECOMMENDATION_TTL, Recommendation, RecType
from app.domain.tenant import TenantContext
from app.infrastructure.db.models import Organization
from app.infrastructure.db.recommendation_store import SqlRecommendationStore
from app.infrastructure.db.session import tenant_session
from app.infrastructure.providers.mock.generator import MockConfig, generate
from tests.unit.fake_reader import FakeReader
from tests.unit.test_assistant_loop import NoForecasts

pytestmark = pytest.mark.db
ROLE = "adpilot_app"
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
DATA = generate(MockConfig(end_date=date(2026, 10, 31), now=NOW))


def _rec(org: uuid.UUID, label: str) -> Recommendation:
    return Recommendation(
        id=uuid.uuid4(),
        organization_id=org,
        type=RecType.BID_CHANGE,
        entity_type="target",
        entity_id=str(uuid.uuid4()),
        entity_label=label,
        current_value=1000,
        proposed_value=900,
        proposed_text=None,
        rationale="r",
        evidence={"rule": "bid_scale_to_target_acos", "n": label},
        confidence="medium",
        expected_impact="direction only",
        data_source="MOCK",
        created_at=NOW,
        expires_at=NOW + RECOMMENDATION_TTL,
    )


async def _org(engine: AsyncEngine, label: str) -> uuid.UUID:
    org = uuid.uuid4()
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        s.add(Organization(id=org, name=label))
        await s.flush()
        await SqlRecommendationStore(s, org).add_new([_rec(org, label)])
    return org


async def _labels(engine: AsyncEngine, org: uuid.UUID) -> list[str]:
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        ctx = ToolContext(
            FakeReader(DATA, org, NOW),
            NoForecasts(),
            recommendations=SqlRecommendationStore(s, org),
            clock=lambda: NOW + timedelta(days=1),
        )
        ev = await ToolRunner(ctx).run(ToolCall("c1", "get_recommendations", {"status": "all"}))
    assert isinstance(ev, Evidence)
    return [i["entity_label"] for i in ev.data["recommendations"]]


async def test_each_organization_sees_only_its_own_proposals(engine: AsyncEngine) -> None:
    a, b = await _org(engine, "alpha-keyword"), await _org(engine, "beta-keyword")
    assert await _labels(engine, a) == ["alpha-keyword"]
    assert await _labels(engine, b) == ["beta-keyword"]
