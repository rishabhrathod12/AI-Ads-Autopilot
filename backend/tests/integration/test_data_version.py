"""The data-version token that keys the analytics cache, on a real PostgreSQL.

Skipped unless TEST_DATABASE_URL is set.
"""

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.sync_service import SyncService
from app.domain.tenant import TenantContext
from app.infrastructure.db.ads_store import SqlAdsStore
from app.infrastructure.db.session import tenant_session
from tests.integration.test_performance_reader import (
    FULL,
    PROVIDER,
    ROLE,
    reader_for,
    seed_org,
)

pytestmark = pytest.mark.db


async def _version(engine: AsyncEngine, org: UUID) -> str:
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        return await reader_for(s, org).data_version()


async def test_the_token_is_stable_between_reads_and_changes_after_a_sync(
    engine: AsyncEngine,
) -> None:
    org = await seed_org(engine)
    before = await _version(engine, org)
    assert before == await _version(engine, org)  # reading never changes it
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        await SyncService(PROVIDER, SqlAdsStore(s, org)).sync(FULL.start, FULL.end)
    assert await _version(engine, org) != before  # even a re-sync of the same days is new data


async def test_an_organization_with_no_data_has_a_token_and_other_organizations_do_not_move_it(
    engine: AsyncEngine,
) -> None:
    quiet, busy = await seed_org(engine), await seed_org(engine)
    quiet_before = await _version(engine, quiet)
    async with tenant_session(engine, TenantContext(busy), ROLE) as s:
        await SyncService(PROVIDER, SqlAdsStore(s, busy)).sync(FULL.start, FULL.end)
    assert await _version(engine, quiet) == quiet_before  # another tenant's sync is invisible
    import uuid

    from app.infrastructure.db.models import Organization

    empty = uuid.uuid4()
    async with tenant_session(engine, TenantContext(empty), ROLE) as s:
        s.add(Organization(id=empty, name="Empty"))
    assert (await _version(engine, empty)).startswith("0|")
