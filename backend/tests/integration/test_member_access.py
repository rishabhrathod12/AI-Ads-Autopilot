"""Invitation sign-in against a real PostgreSQL: linking, "my organizations", and isolation.

Skipped unless TEST_DATABASE_URL is set. Migration 0007 adds the self-read policies these use.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.ports.member_store import AlreadyMemberError
from app.domain.auth import Role
from app.domain.tenant import TenantContext
from app.infrastructure.auth.membership import SqlMembershipDirectory
from app.infrastructure.db.member_store import SqlMemberStore
from app.infrastructure.db.models import Organization
from app.infrastructure.db.session import subject_session, tenant_session

pytestmark = pytest.mark.db
ROLE = "adpilot_app"


def _email() -> str:
    return f"{uuid.uuid4().hex[:10]}@seller.test"


async def _org(engine: AsyncEngine, name: str = "Org") -> uuid.UUID:
    org = uuid.uuid4()
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        s.add(Organization(id=org, name=name))
    return org


async def _invite(engine: AsyncEngine, org: uuid.UUID, email: str, role: Role = Role.ANALYST):  # type: ignore[no-untyped-def]
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        return await SqlMemberStore(s, org).invite(email, role)


async def test_invite_then_first_login_links_and_grants_the_role(engine: AsyncEngine) -> None:
    org, email, subject = await _org(engine), _email(), f"auth0|{uuid.uuid4()}"
    member = await _invite(engine, org, email, Role.ADMIN)
    assert member.linked is False
    directory = SqlMembershipDirectory(engine, ROLE)
    assert await directory.role_for(subject, org) is None  # not yet
    assert await directory.claim_invitation(subject, email.upper()) is True  # case-insensitive
    assert await directory.role_for(subject, org) is Role.ADMIN
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        (listed,) = await SqlMemberStore(s, org).list_members()
    assert listed.linked is True and listed.email == email


async def test_a_linked_invitation_cannot_be_taken_over(engine: AsyncEngine) -> None:
    org, email = await _org(engine), _email()
    await _invite(engine, org, email)
    directory = SqlMembershipDirectory(engine, ROLE)
    assert await directory.claim_invitation("first", email) is True
    assert await directory.claim_invitation("second", email) is False  # already linked
    assert await directory.role_for("second", org) is None
    assert await directory.role_for("first", org) is Role.ANALYST


async def test_one_subject_cannot_claim_two_invitations(engine: AsyncEngine) -> None:
    org = await _org(engine)
    first, second = _email(), _email()
    await _invite(engine, org, first)
    await _invite(engine, org, second)
    directory = SqlMembershipDirectory(engine, ROLE)
    subject = f"sub-{uuid.uuid4()}"
    assert await directory.claim_invitation(subject, first) is True
    assert await directory.claim_invitation(subject, second) is False


async def test_unknown_email_links_nothing(engine: AsyncEngine) -> None:
    directory = SqlMembershipDirectory(engine, ROLE)
    assert await directory.claim_invitation(f"sub-{uuid.uuid4()}", _email()) is False


async def test_organizations_of_lists_only_the_callers_own(engine: AsyncEngine) -> None:
    org_a, org_b, org_c = (
        await _org(engine, "Alpha"),
        await _org(engine, "Beta"),
        await _org(engine),
    )
    mine, theirs = _email(), _email()
    await _invite(engine, org_a, mine, Role.OWNER)
    await _invite(engine, org_b, mine, Role.VIEWER)  # same person, second organization
    await _invite(engine, org_c, theirs, Role.OWNER)
    directory = SqlMembershipDirectory(engine, ROLE)
    me, other = f"me-{uuid.uuid4()}", f"other-{uuid.uuid4()}"
    await directory.claim_invitation(me, mine)
    await directory.claim_invitation(other, theirs)
    got = await directory.organizations_of(me)
    assert [(o.name, o.role) for o in got] == [("Alpha", Role.OWNER), ("Beta", Role.VIEWER)]
    assert {o.organization_id for o in got} == {org_a, org_b}
    assert [o.organization_id for o in await directory.organizations_of(other)] == [org_c]
    assert await directory.organizations_of("nobody") == []


async def test_subject_session_sees_only_its_own_rows_even_with_a_bare_query(
    engine: AsyncEngine,
) -> None:
    org_a, org_b = await _org(engine), await _org(engine)
    mine, theirs = _email(), _email()
    await _invite(engine, org_a, mine)
    await _invite(engine, org_b, theirs)
    subject = f"me-{uuid.uuid4()}"
    await SqlMembershipDirectory(engine, ROLE).claim_invitation(subject, mine)
    async with subject_session(engine, subject, ROLE) as s:
        memberships = (await s.execute(text("SELECT organization_id FROM memberships"))).all()
        orgs = (await s.execute(text("SELECT id FROM organizations"))).all()
    assert [r[0] for r in memberships] == [org_a]
    assert [r[0] for r in orgs] == [org_a]


async def test_an_ordinary_tenant_session_is_unchanged_by_the_new_policies(
    engine: AsyncEngine,
) -> None:
    org_a, org_b = await _org(engine), await _org(engine)
    await _invite(engine, org_a, _email())
    await _invite(engine, org_b, _email())
    async with tenant_session(engine, TenantContext(org_a), ROLE) as s:
        seen = (await s.execute(text("SELECT DISTINCT organization_id FROM memberships"))).all()
        orgs = (await s.execute(text("SELECT id FROM organizations"))).all()
    assert [r[0] for r in seen] == [org_a] and [r[0] for r in orgs] == [org_a]


async def test_no_subject_and_no_tenant_sees_nothing(engine: AsyncEngine) -> None:
    org = await _org(engine)
    await _invite(engine, org, _email())
    async with subject_session(engine, "", ROLE) as s:  # an empty subject must not match anyone
        assert (await s.execute(text("SELECT 1 FROM memberships"))).all() == []
        assert (await s.execute(text("SELECT 1 FROM organizations"))).all() == []


async def test_inviting_twice_or_differing_only_by_case_is_refused(engine: AsyncEngine) -> None:
    org, email = await _org(engine), _email()
    await _invite(engine, org, email)
    with pytest.raises(AlreadyMemberError):
        await _invite(engine, org, email.upper())


async def test_one_person_can_belong_to_two_organizations_with_one_user_row(
    engine: AsyncEngine,
) -> None:
    org_a, org_b, email = await _org(engine), await _org(engine), _email()
    first = await _invite(engine, org_a, email)
    second = await _invite(engine, org_b, email.upper())
    assert first.id != second.id and first.email == second.email


async def test_emails_are_unique_ignoring_case(engine: AsyncEngine) -> None:
    email = _email()
    org = await _org(engine)
    await _invite(engine, org, email)
    with pytest.raises(IntegrityError):
        async with tenant_session(engine, TenantContext(org), ROLE) as s:
            await s.execute(
                text("INSERT INTO users (id, email) VALUES (:id, :e)"),
                {"id": uuid.uuid4(), "e": email.upper()},
            )


async def test_team_listing_is_scoped_to_the_organization(engine: AsyncEngine) -> None:
    org_a, org_b = await _org(engine), await _org(engine)
    a_email, b_email = _email(), _email()
    await _invite(engine, org_a, a_email)
    await _invite(engine, org_b, b_email)
    async with tenant_session(engine, TenantContext(org_a), ROLE) as s:
        assert [m.email for m in await SqlMemberStore(s, org_a).list_members()] == [a_email]
