"""The cache as the analytics routes use it: no stale data, no cross-tenant reads."""

from collections import Counter
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.tenancy import Scope, current_principal, get_scope
from app.config import Settings
from app.domain.auth import Principal, Role
from app.infrastructure.providers.mock.generator import GeneratedData, MockConfig, generate
from tests.unit.fake_reader import FakeReader
from tests.unit.memory_audit_store import MemoryAuditStore
from tests.unit.test_api_analytics import MemoryForecastStore

END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
ORG_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ORG_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
HEAVY = "/api/v1/keywords/findings"


class CountingReader(FakeReader):
    """Counts the heavy aggregate reads, so a test can tell computed from cached."""

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.calls: Counter[str] = Counter()

    async def totals_by_entity(self, *a: Any, **k: Any) -> Any:
        self.calls["totals_by_entity"] += 1
        return await super().totals_by_entity(*a, **k)

    async def daily_totals(self, *a: Any, **k: Any) -> Any:
        self.calls["daily_totals"] += 1
        return await super().daily_totals(*a, **k)


@pytest.fixture(scope="module")
def data() -> GeneratedData:
    return generate(MockConfig(end_date=END, now=NOW))


@pytest.fixture
def api(data: GeneratedData) -> Iterator[tuple[TestClient, dict[UUID, CountingReader]]]:
    app = create_app(Settings(_env_file=None))
    readers = {ORG_A: CountingReader(data, ORG_A, NOW), ORG_B: CountingReader(data, ORG_B, NOW)}

    def org_of(request: Request) -> UUID:
        return UUID(request.headers["X-Test-Org"])

    async def scope(request: Request) -> AsyncIterator[Scope]:
        org = org_of(request)
        yield Scope(org, readers[org], MemoryForecastStore(), MemoryAuditStore())

    async def analyst(request: Request) -> Principal:
        return Principal("u", org_of(request), Role.ANALYST)

    app.dependency_overrides[get_scope] = scope
    app.dependency_overrides[current_principal] = analyst
    with TestClient(app) as client:
        yield client, readers


def get(client: TestClient, path: str, org: UUID = ORG_A) -> Any:
    return client.get(path, headers={"X-Test-Org": str(org)})


def test_a_repeated_request_is_served_from_the_cache_and_is_identical(
    api: tuple[TestClient, dict[UUID, CountingReader]],
) -> None:
    client, readers = api
    first = get(client, HEAVY)
    reads = sum(readers[ORG_A].calls.values())
    second = get(client, HEAVY)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert sum(readers[ORG_A].calls.values()) == reads  # nothing was read for the second request
    text = client.get("/metrics").text
    assert (
        'adpilot_analytics_cache_requests_total{route="keyword_findings",result="miss"} 1' in text
    )
    assert 'adpilot_analytics_cache_requests_total{route="keyword_findings",result="hit"} 1' in text


def test_new_data_is_never_hidden_by_the_cache(
    api: tuple[TestClient, dict[UUID, CountingReader]],
) -> None:
    client, readers = api
    get(client, HEAVY)
    reads = sum(readers[ORG_A].calls.values())
    readers[ORG_A].version = "fake-2"  # a sync ingested something
    get(client, HEAVY)
    assert sum(readers[ORG_A].calls.values()) > reads  # recomputed immediately, not after a timeout


def test_one_organization_never_receives_anothers_cached_result(
    api: tuple[TestClient, dict[UUID, CountingReader]],
) -> None:
    client, readers = api
    get(client, HEAVY, ORG_A)
    assert sum(readers[ORG_B].calls.values()) == 0
    get(client, HEAVY, ORG_B)
    assert sum(readers[ORG_B].calls.values()) > 0  # B computed its own, from its own reader
    reads_b = sum(readers[ORG_B].calls.values())
    get(client, HEAVY, ORG_B)
    assert sum(readers[ORG_B].calls.values()) == reads_b  # and B's second request is B's cache


def test_different_parameters_are_different_cache_entries(
    api: tuple[TestClient, dict[UUID, CountingReader]],
) -> None:
    client, readers = api
    a = get(client, "/api/v1/keywords?sort=spend&limit=5")
    reads = sum(readers[ORG_A].calls.values())
    b = get(client, "/api/v1/keywords?sort=clicks&limit=5")
    assert sum(readers[ORG_A].calls.values()) > reads
    assert a.json() != b.json()
    c = get(client, "/api/v1/keywords?sort=spend&limit=5")
    assert c.json() == a.json()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/dashboard/summary",
        "/api/v1/dashboard/trends",
        "/api/v1/campaigns",
        "/api/v1/campaigns/changes",
        "/api/v1/keywords",
        "/api/v1/search-terms",
        HEAVY,
        "/api/v1/anomalies",
    ],
)
def test_every_cached_route_returns_the_same_body_from_the_cache(
    api: tuple[TestClient, dict[UUID, CountingReader]], path: str
) -> None:
    client, readers = api
    first = get(client, path)
    assert first.status_code == 200, path
    reads = sum(readers[ORG_A].calls.values())
    second = get(client, path)
    assert second.json() == first.json()
    assert sum(readers[ORG_A].calls.values()) == reads, f"{path} was recomputed"


def test_a_failed_request_is_not_cached(
    api: tuple[TestClient, dict[UUID, CountingReader]],
) -> None:
    client, _ = api
    bad = get(client, "/api/v1/keywords?from=2026-10-30&to=2026-10-01")  # from after to
    assert bad.status_code == 422
    assert get(client, "/api/v1/keywords?from=2026-10-30&to=2026-10-01").status_code == 422


def test_the_cache_can_be_switched_off() -> None:
    data = generate(MockConfig(end_date=END, now=NOW))
    app = create_app(Settings(_env_file=None, analytics_cache_ttl_seconds=0))
    reader = CountingReader(data, ORG_A, NOW)

    async def scope() -> AsyncIterator[Scope]:
        yield Scope(ORG_A, reader, MemoryForecastStore(), MemoryAuditStore())

    async def analyst() -> Principal:
        return Principal("u", ORG_A, Role.ANALYST)

    app.dependency_overrides[get_scope] = scope
    app.dependency_overrides[current_principal] = analyst
    with TestClient(app) as client:
        client.get("/api/v1/keywords")
        reads = sum(reader.calls.values())
        client.get("/api/v1/keywords")
        assert sum(reader.calls.values()) == 2 * reads
