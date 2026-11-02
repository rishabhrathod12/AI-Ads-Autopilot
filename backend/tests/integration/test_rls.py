"""Cross-tenant isolation tests against a real PostgreSQL (docs/SECURITY.md section 5)."""

import uuid

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.tenant import TenantContext
from app.infrastructure.db.models import AuditLog, Membership, Organization, User
from app.infrastructure.db.session import tenant_session

pytestmark = pytest.mark.db
ROLE = "adpilot_app"


async def _seed_org(engine: AsyncEngine, name: str) -> tuple[uuid.UUID, uuid.UUID]:
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    async with tenant_session(engine, TenantContext(org_id), ROLE) as s:
        s.add(Organization(id=org_id, name=name))
        s.add(User(id=user_id, email=f"{user_id}@example.test"))
        await s.flush()
        s.add(Membership(user_id=user_id, organization_id=org_id, role="owner"))
        s.add(AuditLog(organization_id=org_id, actor="seed", action="org.created"))
    return org_id, user_id


async def test_app_role_cannot_bypass_rls(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"), {"r": ROLE}
            )
        ).one()
    assert row.rolsuper is False
    assert row.rolbypassrls is False


async def test_tenants_cannot_read_each_other(engine: AsyncEngine) -> None:
    org_a, _ = await _seed_org(engine, "A")
    org_b, _ = await _seed_org(engine, "B")

    async with tenant_session(engine, TenantContext(org_a), ROLE) as s:
        orgs = (await s.scalars(select(Organization.id))).all()
        members = (await s.scalars(select(Membership.organization_id))).all()
        audits = (await s.scalars(select(AuditLog.organization_id))).all()
    assert org_b not in orgs
    assert set(members) <= {org_a}
    assert set(audits) <= {org_a}
    assert org_a in orgs


async def test_no_tenant_set_sees_nothing(engine: AsyncEngine) -> None:
    await _seed_org(engine, "C")
    async with engine.connect() as conn:
        await conn.execute(text(f'SET ROLE "{ROLE}"'))
        for table in ("organizations", "memberships", "audit_logs"):
            count = (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
            assert count == 0, table


async def test_cannot_write_rows_for_another_tenant(engine: AsyncEngine) -> None:
    org_a, user_a = await _seed_org(engine, "D")
    org_b, _ = await _seed_org(engine, "E")
    with pytest.raises(DBAPIError):
        async with tenant_session(engine, TenantContext(org_a), ROLE) as s:
            s.add(AuditLog(organization_id=org_b, actor="attacker", action="x"))
            await s.flush()
    with pytest.raises(DBAPIError):
        async with tenant_session(engine, TenantContext(org_a), ROLE) as s:
            s.add(Membership(user_id=user_a, organization_id=org_b, role="owner"))
            await s.flush()


async def test_audit_logs_are_append_only(engine: AsyncEngine) -> None:
    org, _ = await _seed_org(engine, "F")
    with pytest.raises(ProgrammingError):
        async with tenant_session(engine, TenantContext(org), ROLE) as s:
            await s.execute(update(AuditLog).values(actor="tampered"))
    with pytest.raises(ProgrammingError):
        async with tenant_session(engine, TenantContext(org), ROLE) as s:
            await s.execute(delete(AuditLog))


async def test_tenant_setting_does_not_leak_across_pooled_connections(engine: AsyncEngine) -> None:
    org_a, _ = await _seed_org(engine, "G")
    async with tenant_session(engine, TenantContext(org_a), ROLE):
        pass
    async with engine.connect() as conn:
        value = (await conn.execute(text("SELECT current_setting('app.org_id', true)"))).scalar()
    assert value in (None, "")
