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
