"""The analytics HTTP API: wiring, contracts, validation, and the dev-only tenant guard."""

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.serialize import envelope, to_jsonable
from app.api.tenancy import DEV_ORG_HEADER, Scope, get_scope, resolve_tenant
from app.application.ports.forecast_store import Metric, NewForecastRun, ScopeType, StoredForecast
from app.application.ports.performance_reader import Freshness, Provenance
from app.config import Settings
from app.domain.ads.models import DataSource
from app.infrastructure.providers.mock.generator import GeneratedData, MockConfig, generate
from app.startup_checks import UnsafeConfigurationError
from tests.unit.fake_reader import FakeReader

END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
ORG = UUID("66666666-6666-4666-8666-666666666666")
MATURE_END = END - timedelta(days=3)


class MemoryForecastStore:
    def __init__(self) -> None:
        self.rows: list[StoredForecast] = []

    async def save(self, run: NewForecastRun) -> UUID:
        stored = StoredForecast(**{**run.__dict__, "id": uuid4(), "created_at": NOW})
        self.rows.append(stored)
        return stored.id

    async def get(self, run_id: UUID) -> StoredForecast | None:
        return next((r for r in self.rows if r.id == run_id), None)

    async def latest(
        self, scope_type: ScopeType, scope_id: UUID | None, metric: Metric
    ) -> StoredForecast | None:
        hits = [
            r
            for r in self.rows
            if (r.scope_type, r.scope_id, r.metric) == (scope_type, scope_id, metric)
        ]
        return hits[-1] if hits else None


@pytest.fixture(scope="module")
def data() -> GeneratedData:
    return generate(MockConfig(end_date=END, now=NOW))


@pytest.fixture
def client(data: GeneratedData) -> Iterator[TestClient]:
    app = create_app(Settings(_env_file=None))
    reader, store = FakeReader(data, ORG, NOW), MemoryForecastStore()

    async def scope() -> AsyncIterator[Scope]:
        yield Scope(ORG, reader, store)

    app.dependency_overrides[get_scope] = scope
    with TestClient(app) as c:
        yield c


def get(client: TestClient, path: str, **params: object) -> dict:  # type: ignore[type-arg]
    r = client.get(path, params={k: v for k, v in params.items() if v is not None})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


# ---- tenant guard (no overrides) -----------------------------------------------------------
def test_requests_without_a_dev_tenant_header_are_rejected() -> None:
    with TestClient(create_app(Settings(_env_file=None))) as c:
        r = c.get("/api/v1/dashboard/summary")
        assert r.status_code == 401
        assert r.headers["content-type"].startswith("application/problem+json")
        assert DEV_ORG_HEADER in r.json()["title"]  # HTTP errors carry their message as the title
        assert c.get("/api/v1/anomalies", headers={DEV_ORG_HEADER: "not-a-uuid"}).status_code == 401


def test_a_valid_header_without_a_database_is_503_not_a_leak() -> None:
    with TestClient(create_app(Settings(_env_file=None))) as c:
        r = c.get("/api/v1/dashboard/summary", headers={DEV_ORG_HEADER: str(ORG)})
        assert r.status_code == 503


def test_production_refuses_to_start_until_real_auth_exists() -> None:
    with pytest.raises(UnsafeConfigurationError, match="authentication"):
        create_app(Settings(app_env="production", _env_file=None))


def test_the_dev_resolver_also_refuses_in_production() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=Settings(app_env="production", _env_file=None))
        ),
        headers={DEV_ORG_HEADER: str(ORG)},
    )

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        resolve_tenant(request)  # type: ignore[arg-type]
    assert exc.value.status_code == 401


# ---- contracts -----------------------------------------------------------------------------
def test_every_response_carries_provenance_and_freshness(client: TestClient) -> None:
    for path in (
        "/api/v1/dashboard/summary",
        "/api/v1/dashboard/trends",
        "/api/v1/campaigns",
        "/api/v1/campaigns/changes",
        "/api/v1/keywords",
        "/api/v1/search-terms",
        "/api/v1/keywords/findings",
        "/api/v1/anomalies",
    ):
        meta = get(client, path)["meta"]
        assert meta["data_sources"] == ["MOCK"], path
        assert meta["synthetic"] is True
        assert "not live Amazon data" in meta["notice"]
        assert meta["as_of"] == NOW.isoformat()
        assert meta["latest_date"] == END.isoformat()
        assert meta["mature_through"] == MATURE_END.isoformat()


def test_dashboard_summary_shape_and_money_as_integers(client: TestClient) -> None:
    body = get(client, "/api/v1/dashboard/summary")
    d = body["data"]
    assert d["period"] == {
        "start": (END - timedelta(days=27)).isoformat(),
        "end": END.isoformat(),
        "days": 28,
    }
    assert "trend" not in d  # that is /dashboard/trends
    for field in ("impressions", "clicks", "spend_paise", "sales_paise", "orders", "units"):
        assert type(d["totals"][field]) is int, field
    assert isinstance(d["kpis"]["acos_pct"], float)
    assert d["immature_days"] == 3
    comparison = d["comparison"]
    assert comparison["truncated_days"] == 3
    assert comparison["current"]["end"] == MATURE_END.isoformat()
    change = comparison["changes"]["acos_pct"]
    assert change["absolute"] == pytest.approx(change["current"] - change["previous"], abs=1e-3)
    assert change["relative_pct"] is not None
    assert comparison["acos_explanation"]["main_driver"]["name"] in {"cpc", "cvr", "aov"}
    driver = comparison["acos_explanation"]["drivers"][0]
    assert driver["name"] in {"cpc", "cvr", "aov"}


def test_trends_flag_immature_days_and_carry_kpis(client: TestClient) -> None:
    points = get(client, "/api/v1/dashboard/trends")["data"]["points"]
    assert len(points) == 28
    assert [p["is_mature"] for p in points[-4:]] == [True, False, False, False]
    assert all(p["kpis"]["acos_pct"] is not None for p in points)


def test_explicit_period_is_honoured(client: TestClient) -> None:
    start, end = END - timedelta(days=13), END - timedelta(days=7)
    d = get(
        client, "/api/v1/dashboard/trends", **{"from": start.isoformat(), "to": end.isoformat()}
    )
    assert [p["date"] for p in d["data"]["points"]] == [
        (start + timedelta(days=i)).isoformat() for i in range(7)
    ]


def test_campaign_health_matches_the_planted_scenarios(
    client: TestClient, data: GeneratedData
) -> None:
    rows = get(client, "/api/v1/campaigns")["data"]["rows"]
    assert len(rows) == len(data.campaigns)
    unhealthy = {r["info"]["external_id"] for r in rows if r["health"]["status"] == "unhealthy"}
    constrained = {r["info"]["external_id"] for r in rows if r["budget"]["constrained"]}
    assert unhealthy == set(data.truth.high_acos_campaign_ids)
    assert constrained == set(data.truth.budget_capped_campaign_ids)


def test_campaign_changes_explain_the_planted_anomaly(
    client: TestClient, data: GeneratedData
) -> None:
    rows = get(
        client, "/api/v1/campaigns/changes", **{"from": (END - timedelta(days=13)).isoformat()}
    )
    by_id = {c["info"]["external_id"]: c for c in rows["data"]["campaigns"]}
    for cid in data.truth.anomaly_campaign_ids:
        assert by_id[cid]["acos_explanation"]["drivers"][0]["name"] == "cvr"


def test_keyword_tables_sort_and_limit(client: TestClient) -> None:
    d = get(client, "/api/v1/keywords", limit=10)["data"]
    assert len(d["rows"]) == 10
    assert d["total_rows"] > 10
    spend = [r["totals"]["spend_paise"] for r in d["rows"]]
    assert spend == sorted(spend, reverse=True)
    clicks = [
        r["totals"]["clicks"]
        for r in get(client, "/api/v1/keywords", sort="clicks", limit=5)["data"]["rows"]
    ]
    assert clicks == sorted(clicks, reverse=True)
    terms = get(client, "/api/v1/search-terms", limit=5)["data"]["rows"]
    assert len(terms) == 5
    assert {"term", "kpis", "low_data"} <= set(terms[0])


def test_acos_sort_puts_undefined_acos_first_as_worst(client: TestClient) -> None:
    rows = get(client, "/api/v1/keywords", sort="acos", limit=200)["data"]["rows"]
    defined = [r["kpis"]["acos_pct"] for r in rows if r["kpis"]["acos_pct"] is not None]
    assert defined == sorted(defined, reverse=True)
    undefined_positions = [i for i, r in enumerate(rows) if r["kpis"]["acos_pct"] is None]
    assert undefined_positions == list(range(len(undefined_positions)))  # all before any defined


def test_findings_are_trimmed_but_report_full_counts(client: TestClient) -> None:
    d = get(client, "/api/v1/keywords/findings", limit=5)["data"]
    assert len(d["wasted_targets"]) <= 5
    assert d["counts"]["wasted_targets"] >= len(d["wasted_targets"])
    assert d["counts"]["limit_per_list"] == 5
    levels = [w["level"] for w in d["wasted_targets"]]
    assert levels == sorted(levels, key=lambda lv: lv != "confirmed")
    assert "provenance" not in d
    assert "freshness" not in d


def test_anomalies_endpoint(client: TestClient) -> None:
    d = get(client, "/api/v1/anomalies")["data"]
    assert d["episodes"]
    e = d["episodes"][0]
    assert {"campaign", "metric", "direction", "severity", "status", "onset", "evidence"} <= set(e)
    assert d["monitor"]["end"] == MATURE_END.isoformat()


# ---- validation and errors -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/v1/campaigns", {"target_acos": 0}),
        ("/api/v1/campaigns", {"target_acos": 5000}),
        ("/api/v1/campaigns", {"target_acos": "abc"}),
        ("/api/v1/keywords", {"sort": "color"}),
        ("/api/v1/keywords", {"limit": 0}),
        ("/api/v1/keywords", {"limit": 1000}),
        ("/api/v1/dashboard/summary", {"from": "2026-10-20", "to": "2026-10-01"}),
        ("/api/v1/dashboard/summary", {"from": "2024-01-01", "to": "2026-10-01"}),
        ("/api/v1/dashboard/summary", {"from": "not-a-date"}),
    ],
)
def test_invalid_requests_are_422_problem_details(
    client: TestClient, path: str, params: dict[str, object]
) -> None:
    r = client.get(path, params=params)
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    assert "request_id" in r.json()


def test_a_period_inside_the_attribution_window_is_a_clear_422(client: TestClient) -> None:
    r = client.get("/api/v1/campaigns", params={"from": (END - timedelta(days=1)).isoformat()})
    assert r.status_code == 422
    assert "attribution" in r.json()["detail"]


def test_unknown_route_is_a_problem_404(client: TestClient) -> None:
    assert client.get("/api/v1/nope").status_code == 404


# ---- forecasts -----------------------------------------------------------------------------
def test_forecast_lifecycle(client: TestClient) -> None:
    assert client.get("/api/v1/forecasts/account").status_code == 404  # nothing stored yet

    created = client.post("/api/v1/forecasts/account")
    assert created.status_code == 201, created.text
    body = created.json()
    f = body["data"]["forecasts"]
    assert len(f["spend"]["point"]) == 14
    assert f["spend"]["selected_model"] in f["spend"]["scores"]
    assert f["acos"]["point_pct"] is not None
    assert set(body["data"]["run_ids"]) == {"spend", "sales"}
    assert body["data"]["gap_days"] == 3

    stored = get(client, "/api/v1/forecasts/account")
    assert stored["data"]["spend"]["model"] == f["spend"]["selected_model"]
    assert (
        stored["data"]["spend"]["points"][0]["date"] == (MATURE_END + timedelta(days=1)).isoformat()
    )
    assert stored["data"]["acos_pct"] is not None
    assert stored["meta"]["data_sources"] == ["MOCK"]


# ---- serialisation -------------------------------------------------------------------------
def test_serialiser_conventions() -> None:
    class Color(StrEnum):
        RED = "red"

    uid = uuid4()
    out = to_jsonable(
        {
            "a": Decimal("1.23456789"),
            "n": None,
            "id": uid,
            "c": Color.RED,
            "d": date(2026, 1, 2),
            "l": [1, (2, 3)],
            "f": 0.123456789,
            "b": True,
        }
    )
    assert out == {
        "a": 1.2346,
        "n": None,
        "id": str(uid),
        "c": "red",
        "d": "2026-01-02",
        "l": [1, [2, 3]],
        "f": 0.1235,
        "b": True,
    }
    with pytest.raises(TypeError):
        to_jsonable(object())


def test_envelope_lifts_provenance_and_flags_non_mock_sources() -> None:
    @dataclass
    class Result:
        value: int
        provenance: Provenance
        freshness: Freshness

    prov = Provenance(frozenset({DataSource.AMAZON_ADS_API}), NOW)
    fresh = Freshness(END, MATURE_END)
    body = envelope(Result(1, prov, fresh), prov, fresh)
    assert body["data"] == {"value": 1}
    assert body["meta"]["synthetic"] is False
    assert body["meta"]["notice"] is None
    assert body["meta"]["data_sources"] == ["AMAZON_ADS_API"]
