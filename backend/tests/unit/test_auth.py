"""Authentication and role enforcement. Tokens are signed locally with a throwaway RSA key."""

import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.tenancy import DEV_ORG_HEADER, DEV_ROLE_HEADER, ORG_HEADER, current_principal
from app.application.ports.auth import AuthError
from app.config import Settings
from app.domain.auth import Principal, Role
from app.infrastructure.auth.jwt_verifier import JwtVerifier
from app.startup_checks import UnsafeConfigurationError

ISSUER = "https://idp.example.test/"
AUDIENCE = "adpilot-api"
JWKS = "https://idp.example.test/.well-known/jwks.json"
ORG_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ORG_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_PEM = KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
)


def token(
    sub: str | None = "user-1",
    *,
    key: Any = KEY,
    alg: str = "RS256",
    iss: str = ISSUER,
    aud: str = AUDIENCE,
    exp_in: int = 600,
    extra: dict[str, Any] | None = None,
    drop: tuple[str, ...] = (),
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {"sub": sub, "iss": iss, "aud": aud, "iat": now, "exp": now + exp_in}
    claims.update(extra or {})
    for name in drop:
        claims.pop(name, None)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm=alg)


class FakeDirectory:
    def __init__(self, roles: dict[tuple[str, UUID], Role]) -> None:
        self.roles = roles

    async def role_for(self, subject: str, organization_id: UUID) -> Role | None:
        return self.roles.get((subject, organization_id))


def settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "auth_jwt_issuer": ISSUER,
        "auth_jwt_audience": AUDIENCE,
        "auth_jwks_url": JWKS,
    }
    return Settings(_env_file=None, **(base | kw))


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(settings())
    # Same verifier, but resolving the public key locally instead of fetching a JWKS.
    app.state.token_verifier = JwtVerifier(
        issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _t: KEY.public_key()
    )
    app.state.membership_directory = FakeDirectory(
        {
            ("user-1", ORG_A): Role.ANALYST,
            ("viewer", ORG_A): Role.VIEWER,
            ("owner-b", ORG_B): Role.OWNER,
        }
    )

    @app.get("/_whoami")
    async def whoami(p: Principal = Depends(current_principal)) -> dict[str, str]:
        return {"sub": p.subject, "org": str(p.organization_id), "role": p.role.name}

    with TestClient(app) as c:
        yield c


def call(c: TestClient, tok: str | None, org: UUID | str | None = ORG_A, path: str = "/_whoami"):  # type: ignore[no-untyped-def]
    headers: dict[str, str] = {}
    if tok is not None:
        headers["Authorization"] = f"Bearer {tok}"
    if org is not None:
        headers[ORG_HEADER] = str(org)
    return c.get(path, headers=headers)


# ---- accepted ------------------------------------------------------------------------------
def test_valid_token_and_membership_is_accepted(client: TestClient) -> None:
    r = call(client, token())
    assert r.status_code == 200
    assert r.json() == {"sub": "user-1", "org": str(ORG_A), "role": "ANALYST"}


def test_role_comes_from_membership_not_from_a_token_claim(client: TestClient) -> None:
    r = call(client, token("viewer", extra={"role": "owner", "roles": ["owner"]}))
    assert r.json()["role"] == "VIEWER"


# ---- rejected credentials: always 401, never details ----------------------------------------
@pytest.mark.parametrize(
    "tok",
    [
        pytest.param(None, id="missing"),
        pytest.param("not-a-jwt", id="garbage"),
        pytest.param(token(exp_in=-3600), id="expired"),
        pytest.param(token(iss="https://evil.example/"), id="wrong-issuer"),
        pytest.param(token(aud="other-api"), id="wrong-audience"),
        pytest.param(token(key=OTHER_KEY), id="signed-with-another-key"),
        pytest.param(token(drop=("exp",)), id="no-expiry"),
        pytest.param(token(sub=None), id="no-subject"),
        pytest.param(token(sub=""), id="empty-subject"),
        pytest.param(token(sub="x" * 300), id="oversized-subject"),
        pytest.param("x" * 9000, id="oversized-token"),
    ],
)
def test_bad_tokens_are_401(client: TestClient, tok: str | None) -> None:
    r = call(client, tok)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert "expired" not in r.text and "signature" not in r.text  # no oracle for attackers


def test_alg_none_token_is_rejected(client: TestClient) -> None:
    now = int(time.time())
    claims = {"sub": "user-1", "iss": ISSUER, "aud": AUDIENCE, "exp": now + 600}
    unsigned = jwt.encode(claims, None, algorithm="none")  # type: ignore[arg-type]
    assert call(client, unsigned).status_code == 401


def test_hmac_token_signed_with_the_public_key_is_rejected(client: TestClient) -> None:
    """Algorithm-confusion attack: HS256 keyed with the (public) RSA key bytes."""
    now = int(time.time())

    def b64(obj: dict[str, Any]) -> bytes:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=")

    signing_input = (
        b64({"alg": "HS256", "typ": "JWT"})
        + b"."
        + b64({"sub": "user-1", "iss": ISSUER, "aud": AUDIENCE, "exp": now + 600})
    )
    mac = hmac.new(PUBLIC_PEM, signing_input, hashlib.sha256).digest()
    forged = (signing_input + b"." + base64.urlsafe_b64encode(mac).rstrip(b"=")).decode()
    assert call(client, forged).status_code == 401


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer", "Bearer ", "token abc", ""])
def test_malformed_authorization_header_is_401(client: TestClient, header: str) -> None:
    r = client.get("/_whoami", headers={"Authorization": header, ORG_HEADER: str(ORG_A)})
    assert r.status_code == 401


# ---- tenant selection and isolation --------------------------------------------------------
def test_a_member_of_one_org_cannot_act_in_another(client: TestClient) -> None:
    r = call(client, token("user-1"), org=ORG_B)
    assert r.status_code == 403


def test_a_stranger_with_a_valid_token_is_403(client: TestClient) -> None:
    assert call(client, token("nobody")).status_code == 403


@pytest.mark.parametrize("org", [None, "not-a-uuid", ""])
def test_missing_or_invalid_org_selection_is_400(client: TestClient, org: str | None) -> None:
    assert call(client, token(), org=org).status_code == 400


def test_dev_headers_are_ignored_when_auth_is_configured(client: TestClient) -> None:
    only_dev = client.get("/_whoami", headers={DEV_ORG_HEADER: str(ORG_A)})
    assert only_dev.status_code == 401
    r = client.get(
        "/_whoami",
        headers={
            "Authorization": f"Bearer {token('viewer')}",
            ORG_HEADER: str(ORG_A),
            DEV_ORG_HEADER: str(ORG_B),  # must not redirect the tenant
            DEV_ROLE_HEADER: "owner",  # must not raise the role
        },
    )
    assert r.json() == {"sub": "viewer", "org": str(ORG_A), "role": "VIEWER"}


# ---- roles on real routes ------------------------------------------------------------------
def test_viewer_cannot_create_forecasts_or_use_the_assistant(client: TestClient) -> None:
    h = {"Authorization": f"Bearer {token('viewer')}", ORG_HEADER: str(ORG_A)}
    assert client.post("/api/v1/forecasts/account", headers=h).status_code == 403
    assert client.post("/api/v1/assistant/threads", headers=h).status_code == 403


def test_analyst_passes_authorization_then_hits_the_missing_database(client: TestClient) -> None:
    h = {"Authorization": f"Bearer {token('user-1')}", ORG_HEADER: str(ORG_A)}
    # 503 (no database in unit tests) proves authentication and the role check both passed.
    assert client.post("/api/v1/forecasts/account", headers=h).status_code == 503
    assert client.get("/api/v1/dashboard/summary", headers=h).status_code == 503


def test_every_data_route_demands_credentials(client: TestClient) -> None:
    for method, path in [
        ("get", "/api/v1/dashboard/summary"),
        ("get", "/api/v1/campaigns"),
        ("get", "/api/v1/keywords/findings"),
        ("get", "/api/v1/anomalies"),
        ("post", "/api/v1/forecasts/account"),
        ("post", "/api/v1/assistant/threads"),
    ]:
        assert getattr(client, method)(path).status_code == 401, path


# ---- development mode ----------------------------------------------------------------------
def test_dev_mode_role_header_is_honoured_and_validated() -> None:
    app = create_app(Settings(_env_file=None))

    @app.get("/_whoami")
    async def whoami(p: Principal = Depends(current_principal)) -> dict[str, str]:
        return {"role": p.role.name}

    with TestClient(app) as c:
        h = {DEV_ORG_HEADER: str(ORG_A)}
        assert c.get("/_whoami", headers=h).json() == {"role": "ANALYST"}
        assert c.get("/_whoami", headers=h | {DEV_ROLE_HEADER: "viewer"}).json() == {
            "role": "VIEWER"
        }
        assert c.get("/_whoami", headers=h | {DEV_ROLE_HEADER: "root"}).status_code == 401


# ---- verifier and roles in isolation -------------------------------------------------------
def test_verifier_refuses_symmetric_algorithms_at_construction() -> None:
    with pytest.raises(ValueError, match="asymmetric"):
        JwtVerifier(
            issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _t: "k", algorithms=["HS256"]
        )
    with pytest.raises(ValueError, match="asymmetric"):
        JwtVerifier(issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _t: "k", algorithms=[])


async def test_verifier_failures_carry_no_detail() -> None:
    v = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _t: KEY.public_key())
    with pytest.raises(AuthError) as exc:
        await v.verify(token(exp_in=-3600))
    assert str(exc.value) == ""


async def test_verifier_key_lookup_failure_is_an_auth_error_not_a_crash() -> None:
    def broken(_t: str) -> Any:
        raise OSError("jwks endpoint unreachable")

    v = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, key_resolver=broken)
    with pytest.raises(AuthError):
        await v.verify(token())


def test_roles_are_ordered_and_parse_strictly() -> None:
    assert Role.VIEWER < Role.ANALYST < Role.ADMIN < Role.OWNER
    assert Role.parse("Admin") is Role.ADMIN
    assert Role.parse("root") is None and Role.parse("") is None
    p = Principal("s", uuid.uuid4(), Role.ANALYST)
    assert p.allows(Role.VIEWER) and p.allows(Role.ANALYST) and not p.allows(Role.ADMIN)


# ---- startup configuration -----------------------------------------------------------------
@pytest.mark.parametrize(
    "kw",
    [
        {"auth_jwt_issuer": ISSUER},
        {"auth_jwt_audience": AUDIENCE},
        {"auth_jwks_url": JWKS},
        {"auth_jwt_issuer": ISSUER, "auth_jwt_audience": AUDIENCE},
    ],
)
def test_partial_auth_configuration_is_refused_in_every_environment(kw: dict[str, str]) -> None:
    for env in ("development", "production"):
        with pytest.raises(UnsafeConfigurationError, match="partly configured"):
            create_app(Settings(_env_file=None, app_env=env, **kw))  # type: ignore[arg-type]


def test_production_without_auth_is_refused_and_with_auth_is_accepted() -> None:
    with pytest.raises(UnsafeConfigurationError, match="authentication"):
        create_app(Settings(_env_file=None, app_env="production"))
    create_app(settings(app_env="production"))  # fully configured: starts


def test_unsafe_jwks_urls_and_algorithms_are_refused() -> None:
    with pytest.raises(UnsafeConfigurationError, match="https"):
        create_app(settings(auth_jwks_url="http://idp.example.test/jwks.json"))
    with pytest.raises(UnsafeConfigurationError, match="https"):
        create_app(settings(app_env="production", auth_jwks_url="http://localhost/jwks.json"))
    with pytest.raises(UnsafeConfigurationError, match="asymmetric"):
        create_app(settings(auth_jwt_algorithms=["HS256"]))
    create_app(settings(auth_jwks_url="http://localhost:9000/jwks.json"))  # local dev IdP is fine


def test_empty_strings_mean_unset() -> None:
    assert Settings(_env_file=None, auth_jwt_issuer="").auth_configured is False
    create_app(Settings(_env_file=None, auth_jwt_issuer=""))  # dev, nothing configured: starts
