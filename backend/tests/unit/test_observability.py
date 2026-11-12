"""M7: log and audit hygiene. Nothing secret in logs or audit, every write is audited."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.routers.recommendations import RecoDeps, get_reco_deps
from app.api.tenancy import DEV_ORG_HEADER, DEV_ROLE_HEADER
from app.application.recommendations.service import RecommendationService
from app.config import Settings
from app.infrastructure.providers.mock.generator import MockConfig, generate
from app.logging import REDACTED, configure_logging, exception_details, redact_secrets
from tests.unit.fake_reader import FakeReader
from tests.unit.memory_recommendation_store import MemoryRecommendationStore

ORG = UUID("78787878-7878-4878-8878-787878787878")
NOW = datetime.now(UTC)
SECRETS = [
    "sk-live-abcdef1234567890",
    "hunter2hunter2",
    "alice@example.com",
    "Bearer abcdefgh12345678",
]


def lines(captured: str) -> list[dict[str, Any]]:
    out = []
    for line in captured.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ---- redaction by value --------------------------------------------------------------------
def test_secrets_inside_values_are_redacted_not_just_secret_field_names() -> None:
    event = {
        "event": "call failed: password=hunter2hunter2",
        "detail": {"note": "contact alice@example.com", "items": ["sk-live-abcdef1234567890"]},
        "headers": "Authorization: Bearer abcdefgh12345678",
        "count": 3,
    }
    out = redact_secrets(None, "info", dict(event))
    blob = json.dumps(out)
    for secret in SECRETS:
        assert secret not in blob
    assert out["count"] == 3 and REDACTED in blob


def test_exception_details_have_no_values_and_a_bounded_message() -> None:
    def deep() -> None:
        local_secret = "sk-live-abcdef1234567890"  # noqa: F841, S105 - must never reach the log
        raise RuntimeError("boom password=hunter2hunter2 " + "x" * 1000)

    try:
        deep()
    except RuntimeError as exc:
        d = exception_details(exc)
    blob = json.dumps(d)
    assert "hunter2hunter2" not in blob and "sk-live" not in blob
    assert d["error_type"] == "RuntimeError" and len(d["error_message"]) <= 300
    assert any(f.startswith("test_observability.py:") and f.endswith(" deep") for f in d["frames"])
    assert "/" not in "".join(d["frames"]) and "\\" not in "".join(d["frames"])  # no paths


# ---- access log and error log --------------------------------------------------------------
@pytest.fixture
def app() -> FastAPI:
    configure_logging("INFO")
    app = create_app(Settings(_env_file=None))

    @app.get("/_boom")
    async def boom() -> None:
        raise RuntimeError("failed with password=hunter2hunter2 for alice@example.com")

    return app


def test_access_log_has_route_status_duration_and_tenant_but_no_query_or_credentials(
    app: FastAPI, capsys: pytest.CaptureFixture[str]
) -> None:
    with TestClient(app) as c:
        r = c.get(
            "/api/v1/dashboard/summary?api_key=sk-live-abcdef1234567890&from=2026-01-01",
            headers={
                DEV_ORG_HEADER: str(ORG),
                "Authorization": "Bearer abcdefgh12345678",
                "X-Request-ID": "trace-42",
            },
        )
    assert r.status_code == 503  # no database in unit tests; the request still authenticated
    out = capsys.readouterr().out
    access = [x for x in lines(out) if x.get("event") == "request"]
    assert len(access) == 1
    e = access[0]
    assert e["method"] == "GET" and e["route"] == "/api/v1/dashboard/summary"
    assert e["status"] == 503 and e["request_id"] == "trace-42"
    assert e["org_id"] == str(ORG) and e["role"] == "ANALYST"
    assert isinstance(e["duration_ms"], int | float) and e["duration_ms"] >= 0
    for forbidden in ("sk-live", "abcdefgh", "api_key", "2026-01-01", "Authorization"):
        assert forbidden not in out


def test_access_log_uses_the_route_template_not_the_raw_path(
    app: FastAPI, capsys: pytest.CaptureFixture[str]
) -> None:
    with TestClient(app) as c:
        c.get(f"/api/v1/recommendations/{UUID(int=5)}", headers={DEV_ORG_HEADER: str(ORG)})
        c.get("/no/such/place/with-an-id-12345")
    got = [x for x in lines(capsys.readouterr().out) if x.get("event") == "request"]
    routes = [x["route"] for x in got]
    assert "/api/v1/recommendations/{rec_id}" in routes
    assert "unmatched" in routes
    assert not any(str(UUID(int=5)) in x["route"] or "12345" in x["route"] for x in got)


def test_unhandled_exception_is_logged_safely_and_hidden_from_the_client(
    app: FastAPI, capsys: pytest.CaptureFixture[str]
) -> None:
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/_boom", headers={"X-Request-ID": "trace-boom"})
    assert r.status_code == 500
    assert (
        "hunter2" not in r.text and "alice" not in r.text and r.json()["request_id"] == "trace-boom"
    )
    out = capsys.readouterr().out
    err = [x for x in lines(out) if x.get("event") == "unhandled_exception"]
    assert len(err) == 1
    assert err[0]["request_id"] == "trace-boom" and err[0]["error_type"] == "RuntimeError"
    assert any(f.endswith(" boom") for f in err[0]["frames"])
    for secret in SECRETS:
        assert secret not in out
    access = [x for x in lines(out) if x.get("event") == "request"]
    assert access and access[-1]["status"] == 500


# ---- audit coverage ------------------------------------------------------------------------
@pytest.fixture
def reco_client() -> Iterator[tuple[TestClient, MemoryRecommendationStore]]:
    data = generate(MockConfig(end_date=NOW.date() - timedelta(days=1), now=NOW))
    store = MemoryRecommendationStore(ORG)
    app = create_app(Settings(_env_file=None))

    async def deps() -> Any:
        yield RecoDeps(ORG, FakeReader(data, ORG, NOW), store, RecommendationService(store, ORG))

    app.dependency_overrides[get_reco_deps] = deps
    with TestClient(app) as c:
        yield c, store


H = {DEV_ORG_HEADER: str(ORG)}


def as_role(role: str) -> dict[str, str]:
    return H | {DEV_ROLE_HEADER: role}


def test_generation_and_decision_are_audited_with_actor_and_request_id(
    reco_client: tuple[TestClient, MemoryRecommendationStore],
) -> None:
    c, store = reco_client
    c.post(
        "/api/v1/recommendations/generate", headers=as_role("analyst") | {"X-Request-ID": "gen-1"}
    )
    batch = [e for e in store.audit if e.action == "recommendations.generated"]
    assert len(batch) == 1 and batch[0].request_id == "gen-1" and batch[0].actor == "dev"
    assert batch[0].after and batch[0].after["created"] > 0
    item = c.get("/api/v1/recommendations", headers=H).json()["data"][0]
    c.post(
        f"/api/v1/recommendations/{item['id']}/approve",
        json={
            "content_hash": item["content_hash"],
            "comment": "ok, mail alice@example.com sk-live-abcdef1234567890",
        },
        headers=as_role("admin") | {"X-Request-ID": "approve-1"},
    )
    approved = [e for e in store.audit if e.action == "recommendation.approved"]
    assert approved[0].request_id == "approve-1" and approved[0].entity_id == item["id"]
    blob = json.dumps([e.after for e in store.audit]) + json.dumps(
        [a.comment for a in store.approvals.values()]
    )
    assert "alice@example.com" not in blob and "sk-live" not in blob  # scrubbed before storing
    assert REDACTED in blob


def test_audit_log_filters_and_pages(
    reco_client: tuple[TestClient, MemoryRecommendationStore],
) -> None:
    c, _store = reco_client
    c.post("/api/v1/recommendations/generate", headers=as_role("analyst"))
    admin = as_role("admin")
    everything = c.get("/api/v1/audit-logs?limit=200", headers=admin).json()["data"]
    created = c.get("/api/v1/audit-logs?action=recommendation.created&limit=200", headers=admin)
    assert created.json()["data"] and all(
        e["action"] == "recommendation.created" for e in created.json()["data"]
    )
    one = created.json()["data"][0]["entity"]["id"]
    by_entity = c.get(
        f"/api/v1/audit-logs?entity_type=recommendation&entity_id={one}", headers=admin
    ).json()["data"]
    assert by_entity and all(e["entity"]["id"] == one for e in by_entity)
    page = c.get("/api/v1/audit-logs?limit=2", headers=admin).json()
    assert len(page["data"]) == 2 and page["next_before"] is not None
    assert len(everything) > 2
    assert c.get("/api/v1/audit-logs?limit=0", headers=admin).status_code == 422
    assert c.get("/api/v1/audit-logs", headers=as_role("analyst")).status_code == 403


def test_every_state_changing_route_is_known_and_audited() -> None:
    """A new POST route fails this test until someone decides how it is audited."""
    schema = create_app(Settings(_env_file=None)).openapi()["paths"]
    posts = {path for path, ops in schema.items() if "post" in ops}
    audited = {
        "/api/v1/forecasts/account": "forecast.created",
        "/api/v1/assistant/threads": "assistant.thread_created",
        "/api/v1/assistant/threads/{thread_id}/messages": "assistant.question_asked",
        "/api/v1/recommendations/generate": "recommendations.generated",
        "/api/v1/recommendations/{rec_id}/approve": "recommendation.approved",
        "/api/v1/recommendations/{rec_id}/reject": "recommendation.rejected",
        "/api/v1/recommendations/{rec_id}/execute": "execution.blocked",
    }
    assert posts == set(audited)
