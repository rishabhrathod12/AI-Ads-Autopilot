from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import Settings


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(Settings(_env_file=None))

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_health_reports_mock_and_execution_disabled(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["data_source"] == "MOCK"
    assert body["execution_enabled"] is False


def test_request_id_is_echoed_and_generated(client: TestClient) -> None:
    assert (
        client.get("/health", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"]
        == "abc-123"
    )
    assert client.get("/health").headers["X-Request-ID"]


def test_ready_is_503_without_database(client: TestClient) -> None:
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["status"] == 503


def test_404_is_problem_details(client: TestClient) -> None:
    r = client.get("/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    assert "request_id" in r.json()


def test_unhandled_error_does_not_leak_details(client: TestClient) -> None:
    r = client.get("/boom")
    assert r.status_code == 500
    assert "secret internal detail" not in r.text
