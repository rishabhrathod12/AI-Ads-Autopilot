"""Recommendation API: roles, approval flow, hash binding, expiry, blocked execution, audit."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.routers.recommendations import RecoDeps, get_reco_deps
from app.api.tenancy import DEV_ORG_HEADER, DEV_ROLE_HEADER
from app.application.recommendations.service import RecommendationService
from app.config import Settings
from app.infrastructure.providers.mock.generator import MockConfig, generate
from tests.unit.fake_reader import FakeReader
from tests.unit.memory_recommendation_store import MemoryRecommendationStore
from tests.unit.test_recommendations import rec

ORG = UUID("56565656-5656-4656-8656-565656565656")
NOW = datetime.now(UTC)
END = NOW.date() - timedelta(days=1)
BASE = "/api/v1"


def as_role(role: str) -> dict[str, str]:
    return {DEV_ORG_HEADER: str(ORG), DEV_ROLE_HEADER: role}


@pytest.fixture
def store() -> MemoryRecommendationStore:
    return MemoryRecommendationStore(ORG)


@pytest.fixture
def client(store: MemoryRecommendationStore) -> Iterator[TestClient]:
    data = generate(MockConfig(end_date=END, now=NOW))
    reader = FakeReader(data, ORG, NOW)
    app = create_app(Settings(_env_file=None))

    async def deps() -> Any:
        yield RecoDeps(ORG, reader, store, RecommendationService(store, ORG))

    app.dependency_overrides[get_reco_deps] = deps
    with TestClient(app) as c:
        yield c


def generate_some(client: TestClient) -> dict[str, Any]:
    r = client.post(f"{BASE}/recommendations/generate", headers=as_role("analyst"))
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def first_pending(client: TestClient) -> dict[str, Any]:
    items = client.get(f"{BASE}/recommendations?status=pending", headers=as_role("viewer")).json()
    return items["data"][0]  # type: ignore[no-any-return]


def decide(
    client: TestClient,
    item: dict[str, Any],
    verb: str = "approve",
    *,
    role: str = "admin",
    **kw: Any,
) -> Any:
    body = {"content_hash": item["content_hash"]} | kw
    return client.post(
        f"{BASE}/recommendations/{item['id']}/{verb}", json=body, headers=as_role(role)
    )


# ---- authentication and roles --------------------------------------------------------------
ROUTES = [
    ("get", "/recommendations"),
    ("get", f"/recommendations/{UUID(int=1)}"),
    ("post", "/recommendations/generate"),
    ("post", f"/recommendations/{UUID(int=1)}/approve"),
    ("post", f"/recommendations/{UUID(int=1)}/reject"),
    ("post", f"/recommendations/{UUID(int=1)}/execute"),
    ("get", "/executions"),
    ("get", "/audit-logs"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_route_requires_credentials(client: TestClient, method: str, path: str) -> None:
    assert client.request(method.upper(), BASE + path, json={}).status_code == 401


def test_roles_gate_each_action(client: TestClient) -> None:
    generate_some(client)
    item = first_pending(client)
    # viewer: read only
    assert client.get(f"{BASE}/recommendations", headers=as_role("viewer")).status_code == 200
    assert (
        client.post(f"{BASE}/recommendations/generate", headers=as_role("viewer")).status_code
        == 403
    )
    assert decide(client, item, role="viewer").status_code == 403
    # analyst: may generate, may not decide, execute or read admin data
    assert decide(client, item, role="analyst").status_code == 403
    assert (
        client.post(
            f"{BASE}/recommendations/{item['id']}/execute", headers=as_role("analyst")
        ).status_code
        == 403
    )
    assert client.get(f"{BASE}/audit-logs", headers=as_role("analyst")).status_code == 403
    assert client.get(f"{BASE}/executions", headers=as_role("viewer")).status_code == 403
    # nothing above changed state
    assert first_pending(client)["id"] == item["id"]
    # admin: may decide
    assert decide(client, item, role="admin").status_code == 200


# ---- generation ----------------------------------------------------------------------------
def test_generate_creates_pending_mock_labelled_proposals_once(client: TestClient) -> None:
    first = generate_some(client)["data"]
    assert first["created"] > 0 and first["dropped_by_guardrails"] == 0
    again = generate_some(client)["data"]
    assert again["created"] == 0 and again["already_stored"] == first["proposed"]
    body = client.get(f"{BASE}/recommendations", headers=as_role("viewer")).json()
    assert body["meta"]["synthetic"] is True and "MOCK" in body["meta"]["notice"]
    for r in body["data"]:
        assert r["status"] == "pending" and r["synthetic"] is True and r["evidence"]["rule"]


# ---- approval ------------------------------------------------------------------------------
def test_approval_is_recorded_audited_and_does_not_execute(client: TestClient) -> None:
    generate_some(client)
    item = first_pending(client)
    r = decide(client, item, comment="looks right")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "approved" and data["replayed"] is False
    assert data["approval"]["decided_by"] == "dev"
    assert data["approval"]["content_hash"] == item["content_hash"]
    got = client.get(f"{BASE}/recommendations/{item['id']}", headers=as_role("viewer")).json()
    assert got["data"]["approval"]["comment"] == "looks right"
    actions = [
        e["action"]
        for e in client.get(f"{BASE}/audit-logs", headers=as_role("admin")).json()["data"]
    ]
    assert "recommendation.approved" in actions and "recommendation.created" in actions
    assert client.get(f"{BASE}/executions", headers=as_role("admin")).json()["data"] == []


def test_reject_then_approve_conflicts(client: TestClient) -> None:
    generate_some(client)
    item = first_pending(client)
    assert decide(client, item, "reject").status_code == 200
    assert decide(client, item, "approve").status_code == 409


def test_identical_repeat_is_idempotent_and_writes_nothing_new(
    client: TestClient, store: MemoryRecommendationStore
) -> None:
    generate_some(client)
    item = first_pending(client)
    decide(client, item)
    audits = len(store.audit)
    again = decide(client, item)
    assert again.status_code == 200 and again.json()["data"]["replayed"] is True
    assert len(store.audit) == audits and len(store.approvals) == 1


def test_wrong_or_malformed_hash_is_refused(client: TestClient) -> None:
    generate_some(client)
    item = first_pending(client)
    wrong = client.post(
        f"{BASE}/recommendations/{item['id']}/approve",
        json={"content_hash": "0" * 64},
        headers=as_role("admin"),
    )
    assert wrong.status_code == 409 and "hash" in wrong.json()["title"].lower()
    for bad in ("short", "G" * 64, item["content_hash"].upper()):
        r = client.post(
            f"{BASE}/recommendations/{item['id']}/approve",
            json={"content_hash": bad},
            headers=as_role("admin"),
        )
        assert r.status_code == 422, bad
    assert first_pending(client)["id"] == item["id"]  # still pending


def test_unknown_fields_and_unknown_ids(client: TestClient) -> None:
    generate_some(client)
    item = first_pending(client)
    extra = client.post(
        f"{BASE}/recommendations/{item['id']}/approve",
        json={"content_hash": item["content_hash"], "status": "executed"},
        headers=as_role("admin"),
    )
    assert extra.status_code == 422
    missing = client.post(
        f"{BASE}/recommendations/{UUID(int=404)}/approve",
        json={"content_hash": "a" * 64},
        headers=as_role("admin"),
    )
    assert missing.status_code == 404
    assert (
        client.get(f"{BASE}/recommendations/{UUID(int=404)}", headers=as_role("viewer")).status_code
        == 404
    )


async def test_expired_recommendation_is_refused_and_marked_expired(
    client: TestClient, store: MemoryRecommendationStore
) -> None:
    old = rec(
        organization_id=ORG,
        created_at=NOW - timedelta(days=10),
        expires_at=NOW - timedelta(days=3),
    )
    await store.add_new([old])
    r = decide(client, {"id": str(old.id), "content_hash": old.content_hash})
    assert r.status_code == 409 and "expired" in r.json()["title"].lower()
    shown = client.get(f"{BASE}/recommendations/{old.id}", headers=as_role("viewer")).json()
    assert shown["data"]["status"] == "expired"
    actions = [
        e["action"]
        for e in client.get(f"{BASE}/audit-logs", headers=as_role("admin")).json()["data"]
    ]
    assert "recommendation.expired" in actions


# ---- execution gate ------------------------------------------------------------------------
def test_execution_is_always_blocked_and_recorded(client: TestClient) -> None:
    assert Settings(_env_file=None).ad_execution_enabled is False
    generate_some(client)
    item = first_pending(client)
    decide(client, item)  # even a fully approved recommendation cannot execute
    r = client.post(f"{BASE}/recommendations/{item['id']}/execute", headers=as_role("admin"))
    assert r.status_code == 409
    assert "execution_disabled" in r.json()["detail"] and "No change was sent" in r.json()["detail"]
    attempts = client.get(f"{BASE}/executions", headers=as_role("admin")).json()["data"]
    assert len(attempts) == 1
    assert attempts[0]["status"] == "blocked" and attempts[0]["dry_run"] is True
    assert attempts[0]["reason"] == "execution_disabled"
    actions = [
        e["action"]
        for e in client.get(f"{BASE}/audit-logs", headers=as_role("admin")).json()["data"]
    ]
    assert actions[0] == "execution.blocked"


def test_execute_unknown_recommendation_is_404(client: TestClient) -> None:
    r = client.post(f"{BASE}/recommendations/{UUID(int=7)}/execute", headers=as_role("admin"))
    assert r.status_code == 404
