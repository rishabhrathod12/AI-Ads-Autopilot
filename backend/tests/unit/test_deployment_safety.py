"""Production-only safeguards: no API docs, and a refusal to serve as a role that bypasses RLS."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import Settings
from app.startup_checks import UnsafeConfigurationError, assert_rls_enforced

PROD_AUTH: dict[str, Any] = {
    "auth_jwt_issuer": "https://idp.example.test/",
    "auth_jwt_audience": "adpilot-api",
    "auth_jwks_url": "https://idp.example.test/.well-known/jwks.json",
}
DOC_PATHS = ["/docs", "/redoc", "/openapi.json"]


def test_api_description_is_published_in_development_only() -> None:
    with TestClient(create_app(Settings(_env_file=None))) as dev:
        assert all(dev.get(p).status_code == 200 for p in DOC_PATHS)
    with TestClient(
        create_app(Settings(_env_file=None, app_env="production", **PROD_AUTH))
    ) as prod:
        assert all(prod.get(p).status_code == 404 for p in DOC_PATHS)
        assert prod.get("/health").status_code == 200  # the service itself is unaffected


def fake_session(flags: SimpleNamespace, calls: list[Any]) -> Any:
    @asynccontextmanager
    async def tenant_session(engine: Any, ctx: Any, role: Any = None) -> Any:
        calls.append(role)

        class Result:
            def one(self) -> SimpleNamespace:
                return flags

        class Session:
            async def execute(self, _stmt: Any) -> Result:
                return Result()

        yield Session()

    return tenant_session


@pytest.mark.parametrize(
    ("flags", "refused"),
    [
        (SimpleNamespace(rolsuper=False, rolbypassrls=False), False),
        (SimpleNamespace(rolsuper=True, rolbypassrls=False), True),
        (SimpleNamespace(rolsuper=False, rolbypassrls=True), True),
        (SimpleNamespace(rolsuper=True, rolbypassrls=True), True),
    ],
)
async def test_rls_bypassing_roles_are_refused(
    monkeypatch: pytest.MonkeyPatch, flags: SimpleNamespace, refused: bool
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr("app.startup_checks.tenant_session", fake_session(flags, calls))
    if refused:
        with pytest.raises(UnsafeConfigurationError, match="bypass row-level security"):
            await assert_rls_enforced(object(), "adpilot_app")  # type: ignore[arg-type]
    else:
        await assert_rls_enforced(object(), "adpilot_app")  # type: ignore[arg-type]
    assert calls == ["adpilot_app"]  # checked as the role the app really uses per request


# ---- container sign-in wiring (ADR 0026): text checks, since Docker is not available here ----
def _repo_file(*parts: str) -> str:
    from pathlib import Path

    return Path(__file__).resolve().parents[3].joinpath(*parts).read_text(encoding="utf-8")


def test_every_nginx_csp_allows_the_auth_provider_through_the_build_placeholder() -> None:
    conf = _repo_file("frontend", "nginx.conf")
    csp_lines = [ln for ln in conf.splitlines() if "Content-Security-Policy" in ln]
    assert len(csp_lines) == 2  # server level and the "/" location (which drops server headers)
    for line in csp_lines:
        assert "connect-src 'self' __AUTH0_ORIGIN__;" in line
        assert "frame-src 'self' __AUTH0_ORIGIN__;" in line  # silent token renewal uses an iframe
        assert "script-src 'self';" in line and "unsafe-eval" not in line  # not loosened


def test_dockerfile_substitutes_only_a_plain_hostname_and_fails_on_leftovers() -> None:
    dockerfile = _repo_file("frontend", "Dockerfile")
    assert "[!A-Za-z0-9.-]" in dockerfile  # anything else fails the build
    assert "! grep -q" in dockerfile and "__AUTH0_ORIGIN__" in dockerfile
    for name in ("VITE_AUTH0_DOMAIN", "VITE_AUTH0_CLIENT_ID", "VITE_AUTH0_AUDIENCE"):
        assert f"ARG {name}=" in dockerfile
    compose = _repo_file("docker-compose.prod.yml")
    for name in ("VITE_AUTH0_DOMAIN", "VITE_AUTH0_CLIENT_ID", "VITE_AUTH0_AUDIENCE"):
        assert f"{name}: ${{{name}:-}}" in compose
