"""SQL audit store against a real PostgreSQL: filters, paging, tenant isolation, immutability.

UNVERIFIED in the environment where this was written (no test database was available): skipped
there. Run with TEST_DATABASE_URL set before relying on the audit query filters.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.audit import audit_entry
from app.domain.tenant import TenantContext
from app.infrastructure.db.audit_store import SqlAuditStore
from app.infrastructure.db.models import AuditLog, Organization
from app.infrastructure.db.session import tenant_session

pytestmark = pytest.mark.db
ROLE = "adpilot_app"
T0 = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)


async def new_org(engine: AsyncEngine) -> uuid.UUID:
    org = uuid.uuid4()
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        s.add(Organization(id=org, name="O"))
    return org


def entry(org: uuid.UUID, action: str, entity_id: str, minutes: int):  # type: ignore[no-untyped-def]
    return audit_entry(
        org=org,
        actor="admin",
        action=action,
        entity_type="recommendation",
        entity_id=entity_id,
        now=T0 + timedelta(minutes=minutes),
        request_id=f"req-{minutes}",
        after={"comment": "mail alice@example.com"},
    )


async def test_filters_paging_and_scrubbing(engine: AsyncEngine) -> None:
    org = await new_org(engine)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        store = SqlAuditStore(s, org)
        for i, (action, ent) in enumerate(
            [("a.created", "r1"), ("a.approved", "r1"), ("a.created", "r2"), ("a.approved", "r2")]
        ):
            await store.add_audit(entry(org, action, ent, i))
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        store = SqlAuditStore(s, org)
        assert [e.action for e in await store.audit_log(action="a.approved")] == [
            "a.approved",
            "a.approved",
        ]
        assert {e.entity_id for e in await store.audit_log(entity_id="r1")} == {"r1"}
        newest = await store.audit_log(limit=2)
        assert [e.request_id for e in newest] == ["req-3", "req-2"]
        older = await store.audit_log(before=newest[-1].created_at)
        assert [e.request_id for e in older] == ["req-1", "req-0"]
        assert "alice@example.com" not in repr(await store.audit_log())


async def test_tenants_cannot_read_or_write_each_others_audit(engine: AsyncEngine) -> None:
    a, b = await new_org(engine), await new_org(engine)
    async with tenant_session(engine, TenantContext(a), ROLE) as s:
        await SqlAuditStore(s, a).add_audit(entry(a, "a.created", "r1", 0))
    async with tenant_session(engine, TenantContext(b), ROLE) as s:
        other = SqlAuditStore(s, b)
        assert await other.audit_log() == []
        with pytest.raises(ValueError, match="another organization"):
            await other.add_audit(entry(a, "a.created", "r1", 1))


async def test_audit_rows_cannot_be_changed_or_removed(engine: AsyncEngine) -> None:
    org = await new_org(engine)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        await SqlAuditStore(s, org).add_audit(entry(org, "a.created", "r1", 0))
    for stmt in (update(AuditLog).values(actor="x"), delete(AuditLog)):
        with pytest.raises((ProgrammingError, DBAPIError)):
            async with tenant_session(engine, TenantContext(org), ROLE) as s:
                await s.execute(stmt)
