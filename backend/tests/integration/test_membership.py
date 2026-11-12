"""Membership lookup against a real PostgreSQL: roles resolve per organization, never across.

UNVERIFIED in the development environment where this was written (no test database was available):
it is skipped there and must be run with TEST_DATABASE_URL set before relying on it.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.auth import Role
from app.domain.tenant import TenantContext
from app.infrastructure.auth.membership import SqlMembershipDirectory
from app.infrastructure.db.models import Membership, Organization, User
from app.infrastructure.db.session import tenant_session

pytestmark = pytest.mark.db
ROLE = "adpilot_app"


async def _org_with_member(
    engine: AsyncEngine, subject: str, role: str
) -> tuple[uuid.UUID, uuid.UUID]:
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    async with tenant_session(engine, TenantContext(org_id), ROLE) as s:
        s.add(Organization(id=org_id, name="O"))
        s.add(User(id=user_id, email=f"{user_id}@example.test", auth_subject=subject))
        await s.flush()
        s.add(Membership(user_id=user_id, organization_id=org_id, role=role))
    return org_id, user_id


async def test_role_resolves_for_a_member(engine: AsyncEngine) -> None:
    subject = f"sub-{uuid.uuid4()}"
    org, _ = await _org_with_member(engine, subject, "admin")
    directory = SqlMembershipDirectory(engine, ROLE)
    assert await directory.role_for(subject, org) is Role.ADMIN


async def test_a_member_of_one_org_has_no_role_in_another(engine: AsyncEngine) -> None:
    subject = f"sub-{uuid.uuid4()}"
    org_a, _ = await _org_with_member(engine, subject, "owner")
    org_b, _ = await _org_with_member(engine, f"other-{uuid.uuid4()}", "owner")
    directory = SqlMembershipDirectory(engine, ROLE)
    assert await directory.role_for(subject, org_a) is Role.OWNER
    assert await directory.role_for(subject, org_b) is None


async def test_unknown_subject_and_unknown_role_value_get_no_access(engine: AsyncEngine) -> None:
    subject = f"sub-{uuid.uuid4()}"
    org, _ = await _org_with_member(engine, subject, "superadmin")  # not a defined role
    directory = SqlMembershipDirectory(engine, ROLE)
    assert await directory.role_for(subject, org) is None
    assert await directory.role_for("nobody", org) is None
