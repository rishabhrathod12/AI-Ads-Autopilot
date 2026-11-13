"""Invitation sign-in: email claims, first-login linking, /me and the team routes (ADR 0026)."""

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.routers.members import MemberDeps, get_member_deps
from app.api.tenancy import DEV_ORG_HEADER, DEV_ROLE_HEADER, ORG_HEADER
from app.application.ports.auth import OrganizationAccess
from app.config import Settings
from app.domain.auth import Role
from app.infrastructure.auth.jwt_verifier import JwtVerifier
from tests.unit.memory_audit_store import MemoryAuditStore
from tests.unit.memory_member_store import MemoryMemberStore
from tests.unit.test_auth import AUDIENCE, ISSUER, KEY, ORG_A, ORG_B, settings, token


class InvitingDirectory:
    """Roles by (subject, org); an invitation links an email to a subject on first claim."""

    def __init__(self) -> None:
        self.roles: dict[tuple[str, UUID], Role] = {}
        self.invitations: dict[str, tuple[UUID, Role]] = {}  # email -> (org, role)
        self.claims: list[tuple[str, str]] = []

    async def role_for(self, subject: str, organization_id: UUID) -> Role | None:
        return self.roles.get((subject, organization_id))

    async def claim_invitation(self, subject: str, email: str) -> bool:
        self.claims.append((subject, email))
        invited = self.invitations.pop(email, None)
        if invited is None:
            return False
        org, role = invited
        self.roles[(subject, org)] = role
        return True

    async def organizations_of(self, subject: str) -> list[OrganizationAccess]:
        return [
            OrganizationAccess(org, f"Org {str(org)[:4]}", role)
            for (sub, org), role in sorted(self.roles.items(), key=lambda kv: str(kv[0][1]))
            if sub == subject
        ]


EMAIL_CLAIMS = {"email": "new@seller.test", "email_verified": True}


@pytest.fixture
def directory() -> InvitingDirectory:
    d = InvitingDirectory()
    d.roles[("admin-1", ORG_A)] = Role.ADMIN
    d.roles[("analyst-1", ORG_A)] = Role.ANALYST
    d.roles[("owner-1", ORG_A)] = Role.OWNER
    d.roles[("owner-1", ORG_B)] = Role.OWNER
    d.invitations["new@seller.test"] = (ORG_A, Role.ANALYST)
    return d


@pytest.fixture
def team() -> MemoryMemberStore:
    return MemoryMemberStore()


@pytest.fixture
def audit() -> MemoryAuditStore:
    return MemoryAuditStore()


@pytest.fixture
def client(
    directory: InvitingDirectory, team: MemoryMemberStore, audit: MemoryAuditStore
) -> Iterator[TestClient]:
    app = create_app(settings())
    app.state.token_verifier = JwtVerifier(
        issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _t: KEY.public_key()
    )
    app.state.membership_directory = directory

    async def deps() -> MemberDeps:
        return MemberDeps(ORG_A, team, audit)

    app.dependency_overrides[get_member_deps] = deps
    with TestClient(app) as c:
        yield c


def auth(sub: str, extra: dict[str, Any] | None = None, org: UUID | None = ORG_A) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token(sub, extra=extra)}"}
    if org is not None:
        headers[ORG_HEADER] = str(org)
    return headers


# ---- the verifier reads the email claims, and trusts only a literal true --------------------
@pytest.mark.parametrize(
    ("claims", "email", "verified"),
    [
        ({"email": "A@Seller.Test", "email_verified": True}, "a@seller.test", True),
        ({"email": "a@seller.test", "email_verified": False}, "a@seller.test", False),
        ({"email": "a@seller.test", "email_verified": "true"}, "a@seller.test", False),
        ({"email": "a@seller.test", "email_verified": 1}, "a@seller.test", False),
        ({"email": "a@seller.test"}, "a@seller.test", False),
        ({}, None, False),
        ({"email": 42, "email_verified": True}, None, False),
        ({"email": "x" * 400, "email_verified": True}, None, False),
    ],
)
async def test_verifier_email_claims(claims: dict[str, Any], email: str | None, verified: bool):  # type: ignore[no-untyped-def]
    v = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _t: KEY.public_key())
    got = await v.verify(token("u", extra=claims))
    assert (got.email, got.email_verified) == (email, verified)


async def test_verifier_honours_namespaced_claim_names() -> None:
    v = JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        key_resolver=lambda _t: KEY.public_key(),
        email_claim="https://adpilot.test/email",
        email_verified_claim="https://adpilot.test/email_verified",
    )
    plain = await v.verify(token("u", extra=EMAIL_CLAIMS))
    assert plain.email is None  # the default names are not read when others are configured
    namespaced = await v.verify(
        token(
            "u",
            extra={
                "https://adpilot.test/email": "n@seller.test",
                "https://adpilot.test/email_verified": True,
            },
        )
    )
    assert (namespaced.email, namespaced.email_verified) == ("n@seller.test", True)


# ---- first login links an invited user ------------------------------------------------------
def test_invited_user_gets_access_on_first_request(
    client: TestClient, directory: InvitingDirectory
) -> None:
    r = client.get("/api/v1/members", headers=auth("sub-new", EMAIL_CLAIMS))
    assert r.status_code == 403  # linked as analyst, but the team route needs admin
    assert directory.roles[("sub-new", ORG_A)] is Role.ANALYST
    assert directory.claims == [("sub-new", "new@seller.test")]


def test_unverified_email_never_links(client: TestClient, directory: InvitingDirectory) -> None:
    unverified = {"email": "new@seller.test", "email_verified": False}
    assert client.get("/api/v1/members", headers=auth("attacker", unverified)).status_code == 403
    assert directory.claims == []
    assert ("attacker", ORG_A) not in directory.roles


def test_member_requests_do_not_attempt_linking(
    client: TestClient, directory: InvitingDirectory
) -> None:
    client.get("/api/v1/members", headers=auth("admin-1", EMAIL_CLAIMS))
    assert directory.claims == []  # an existing member is resolved without touching invitations


def test_a_stranger_without_an_invitation_stays_out(client: TestClient) -> None:
    extra = {"email": "nobody@seller.test", "email_verified": True}
    assert client.get("/api/v1/members", headers=auth("stranger", extra)).status_code == 403


# ---- /me ------------------------------------------------------------------------------------
def test_me_lists_the_callers_organizations_without_choosing_one(client: TestClient) -> None:
    r = client.get("/api/v1/me", headers=auth("owner-1", org=None))
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["subject"] == "owner-1"
    assert {(o["id"], o["role"]) for o in body["organizations"]} == {
        (str(ORG_A), "owner"),
        (str(ORG_B), "owner"),
    }


def test_me_links_an_invitation_and_then_lists_it(client: TestClient) -> None:
    r = client.get("/api/v1/me", headers=auth("sub-new", EMAIL_CLAIMS, org=None))
    assert r.json()["data"]["organizations"][0]["id"] == str(ORG_A)
    assert r.json()["data"]["organizations"][0]["role"] == "analyst"


def test_me_for_a_user_with_no_organizations_is_empty_not_an_error(client: TestClient) -> None:
    r = client.get("/api/v1/me", headers=auth("nobody", org=None))
    assert r.status_code == 200 and r.json()["data"]["organizations"] == []


def test_me_requires_a_valid_token_and_is_absent_in_dev_mode(client: TestClient) -> None:
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me", headers={"Authorization": "Bearer junk"}).status_code == 401
    with TestClient(create_app(Settings(_env_file=None))) as dev:  # nothing configured: dev mode
        assert dev.get("/api/v1/me", headers={DEV_ORG_HEADER: str(ORG_A)}).status_code == 404


# ---- team routes ----------------------------------------------------------------------------
def test_admin_invites_and_the_invitation_is_listed_and_audited(
    client: TestClient, audit: MemoryAuditStore
) -> None:
    r = client.post(
        "/api/v1/members",
        json={"email": "  Ana@Seller.Test ", "role": "analyst"},
        headers=auth("admin-1"),
    )
    assert r.status_code == 201
    data = r.json()["data"]
    assert (data["email"], data["role"], data["status"]) == (
        "ana@seller.test",
        "analyst",
        "invited",
    )
    listed = client.get("/api/v1/members", headers=auth("admin-1")).json()["data"]
    assert [m["email"] for m in listed] == ["ana@seller.test"]
    (entry,) = audit.entries
    assert entry.action == "member.invited" and entry.entity_id == data["id"]
    assert entry.actor == "admin-1" and entry.after == {"role": "analyst"}
    assert "seller.test" not in repr(entry)  # the address itself is not written to the audit log


def test_only_admins_can_use_the_team_routes(client: TestClient) -> None:
    body = {"email": "x@seller.test", "role": "viewer"}
    assert client.post("/api/v1/members", json=body, headers=auth("analyst-1")).status_code == 403
    assert client.get("/api/v1/members", headers=auth("analyst-1")).status_code == 403
    assert client.post("/api/v1/members", json=body).status_code == 401


def test_nobody_can_grant_a_role_above_their_own(client: TestClient) -> None:
    owner = {"email": "boss@seller.test", "role": "owner"}
    assert client.post("/api/v1/members", json=owner, headers=auth("admin-1")).status_code == 403
    assert client.post("/api/v1/members", json=owner, headers=auth("owner-1")).status_code == 201


def test_duplicate_bad_email_and_unknown_role_are_refused(client: TestClient) -> None:
    ok = {"email": "dup@seller.test", "role": "viewer"}
    assert client.post("/api/v1/members", json=ok, headers=auth("admin-1")).status_code == 201
    again = client.post(
        "/api/v1/members", json=ok | {"email": "DUP@seller.test"}, headers=auth("admin-1")
    )
    assert again.status_code == 409
    for bad in (
        {"email": "not-an-email", "role": "viewer"},
        {"email": "a b@c.d", "role": "viewer"},
    ):
        assert client.post("/api/v1/members", json=bad, headers=auth("admin-1")).status_code == 422
    for role in ("root", "OWNER ", ""):
        r = client.post(
            "/api/v1/members",
            json={"email": "r@seller.test", "role": role},
            headers=auth("admin-1"),
        )
        assert r.status_code == 422
    extra = {"email": "e@seller.test", "role": "viewer", "organization_id": str(ORG_B)}
    assert client.post("/api/v1/members", json=extra, headers=auth("admin-1")).status_code == 422


def test_dev_headers_cannot_reach_team_routes_when_auth_is_configured(client: TestClient) -> None:
    h = {DEV_ORG_HEADER: str(ORG_A), DEV_ROLE_HEADER: "owner"}
    assert client.get("/api/v1/members", headers=h).status_code == 401
