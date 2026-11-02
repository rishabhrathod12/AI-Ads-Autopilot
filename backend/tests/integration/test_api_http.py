"""The HTTP API against a real PostgreSQL: tenant scoping end to end."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.tenancy import DEV_ORG_HEADER
from app.application.sync_service import SyncService
from app.config import Settings
from app.domain.tenant import TenantContext
from app.infrastructure.db.ads_store import SqlAdsStore
from app.infrastructure.db.models import Organization
from app.infrastructure.db.session import create_engine, tenant_session
from app.infrastructure.providers.mock.generator import MockConfig
from app.infrastructure.providers.mock.provider import MockAdvertisingDataProvider

pytestmark = pytest.mark.db
ROLE = "adpilot_app"
END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
CFG = MockConfig(
    days=30,
    end_date=END,
    n_campaigns=6,
    ad_groups_per_campaign=2,
    keywords_per_ad_group=8,
    now=NOW,
)


async def seed(url: str) -> UUID:
    org = uuid4()
    engine = create_engine(url)
    try:
        async with tenant_session(engine, TenantContext(org), ROLE) as s:
            s.add(Organization(id=org, name="HTTP test"))
            await s.flush()
            await SyncService(MockAdvertisingDataProvider(CFG), SqlAdsStore(s, org)).sync(
                END - timedelta(days=29), END
            )
    finally:
        await engine.dispose()
    return org


def test_each_tenant_sees_only_its_own_data_over_http(migrated_db: str) -> None:
    org_a = asyncio.run(seed(migrated_db))
    org_b = uuid4()  # a tenant with no data at all
    app = create_app(Settings(database_url=migrated_db, db_app_role=ROLE, _env_file=None))
    with TestClient(app) as client:
        a = {DEV_ORG_HEADER: str(org_a)}
        b = {DEV_ORG_HEADER: str(org_b)}

        summary = client.get("/api/v1/dashboard/summary", headers=a)
        assert summary.status_code == 200, summary.text
        body = summary.json()
        assert body["data"]["totals"]["clicks"] > 0
        assert body["meta"]["data_sources"] == ["MOCK"]
        assert body["meta"]["synthetic"] is True

        campaigns = client.get("/api/v1/campaigns", headers=a)
        assert campaigns.status_code == 200, campaigns.text
        assert len(campaigns.json()["data"]["rows"]) == 6

        keywords = client.get("/api/v1/keywords", params={"limit": 5}, headers=a)
        assert keywords.status_code == 200
        assert keywords.json()["data"]["total_rows"] > 5

        # Tenant B has no data: a clean 404, never tenant A's numbers.
        for path in ("/api/v1/dashboard/summary", "/api/v1/campaigns", "/api/v1/keywords"):
            other = client.get(path, headers=b)
            assert other.status_code == 404, path
            assert "totals" not in other.text

        assert client.get("/api/v1/dashboard/summary").status_code == 401
        assert client.get("/api/v1/forecasts/account", headers=b).status_code == 404
