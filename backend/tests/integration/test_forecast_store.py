"""Stored forecasts: round trip, immutability, tenant isolation and constraints."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.forecasting.models import MovingAverage7, Naive, SeasonalMean4, SeasonalNaive
from app.application.forecasting.records import save_account_forecast
from app.application.forecasting.reports import forecast_account
from app.application.ports.forecast_store import NewForecastRun
from app.domain.ads.models import DataSource
from app.domain.tenant import TenantContext
from app.infrastructure.db import forecast_models  # noqa: F401
from app.infrastructure.db.forecast_store import SqlForecastStore
from app.infrastructure.db.models import Organization
from app.infrastructure.db.session import tenant_session
from app.infrastructure.providers.mock.generator import MockConfig, generate
from tests.unit.fake_reader import FakeReader

pytestmark = pytest.mark.db
ROLE = "adpilot_app"
END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)


def run(**over: object) -> NewForecastRun:
    base: dict[str, object] = {
        "scope_type": "account",
        "scope_id": None,
        "metric": "spend",
        "horizon_start": END + timedelta(days=1),
        "horizon_end": END + timedelta(days=14),
        "train_start": END - timedelta(days=99),
        "train_end": END,
        "train_days": 100,
        "model": "seasonal_naive",
        "used_baseline": True,
        "params": {"reason": "test", "scores": {"naive": {"mae": 1.5}}},
        "points": [{"date": "2026-11-01", "point": 1.0, "lower": 0.5, "upper": 1.5}],
        "data_source": DataSource.MOCK,
        "input_as_of": NOW,
    }
    return NewForecastRun(**{**base, **over})  # type: ignore[arg-type]


async def new_org(engine: AsyncEngine) -> UUID:
    org = uuid4()
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        s.add(Organization(id=org, name="Forecast test"))
    return org


async def test_round_trip_and_latest(engine: AsyncEngine) -> None:
    org = await new_org(engine)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        store = SqlForecastStore(s, org)
        first = await store.save(run(model="naive"))
        second = await store.save(run(model="ets", used_baseline=False))
        got = await store.get(first)
        latest = await store.latest("account", None, "spend")
        other_metric = await store.latest("account", None, "sales")
    assert got is not None
    assert got.model == "naive"
    assert got.params["scores"]["naive"]["mae"] == 1.5
    assert got.points[0]["date"] == "2026-11-01"
    assert got.data_source is DataSource.MOCK
    assert got.input_as_of == NOW
    assert latest is not None
    assert latest.id == second
    assert other_metric is None


async def test_stored_forecasts_are_immutable(engine: AsyncEngine) -> None:
    org = await new_org(engine)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        await SqlForecastStore(s, org).save(run())
    for statement in ("UPDATE forecast_runs SET model = 'x'", "DELETE FROM forecast_runs"):
        with pytest.raises(ProgrammingError):
            async with tenant_session(engine, TenantContext(org), ROLE) as s:
                await s.execute(text(statement))


async def test_other_tenants_cannot_read_or_write(engine: AsyncEngine) -> None:
    org_a, org_b = await new_org(engine), await new_org(engine)
    async with tenant_session(engine, TenantContext(org_a), ROLE) as s:
        run_id = await SqlForecastStore(s, org_a).save(run())
    async with tenant_session(engine, TenantContext(org_b), ROLE) as s:
        store = SqlForecastStore(s, org_b)
        assert await store.get(run_id) is None
        assert await store.latest("account", None, "spend") is None
        assert await SqlForecastStore(s, org_a).get(run_id) is None  # RLS holds even when asked
    with pytest.raises(DBAPIError):  # WITH CHECK: cannot insert a row owned by another tenant
        async with tenant_session(engine, TenantContext(org_b), ROLE) as s:
            await SqlForecastStore(s, org_a).save(run())


@pytest.mark.parametrize(
    "bad",
    [
        {"scope_type": "account", "scope_id": uuid4()},  # an account forecast has no campaign
        {"scope_type": "campaign", "scope_id": None},  # a campaign forecast needs one
        {"metric": "acos"},  # ACOS is derived, never stored as a forecast of its own
        {"horizon_end": END - timedelta(days=30)},
    ],
)
async def test_database_rejects_inconsistent_forecasts(
    engine: AsyncEngine, bad: dict[str, object]
) -> None:
    org = await new_org(engine)
    with pytest.raises(DBAPIError):
        async with tenant_session(engine, TenantContext(org), ROLE) as s:
            await SqlForecastStore(s, org).save(run(**bad))


async def test_account_forecast_is_saved_with_its_evidence(engine: AsyncEngine) -> None:
    data = generate(
        MockConfig(
            days=100,
            end_date=END,
            n_campaigns=6,
            ad_groups_per_campaign=2,
            keywords_per_ad_group=10,
            now=NOW,
        )
    )
    org = await new_org(engine)
    result = await forecast_account(
        FakeReader(data, org, NOW),
        forecasters=[Naive(), MovingAverage7(), SeasonalNaive(), SeasonalMean4()],
    )
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        store = SqlForecastStore(s, org)
        ids = await save_account_forecast(store, result)
        spend = await store.get(ids["spend"])
        sales = await store.latest("account", None, "sales")
    assert set(ids) == {"spend", "sales"}
    assert spend is not None
    assert sales is not None
    assert spend.train_end == END - timedelta(days=3)  # mature days only
    assert spend.horizon_start == spend.train_end + timedelta(days=1)
    assert len(spend.points) == 14
    assert spend.data_source is DataSource.MOCK
    assert spend.input_as_of == NOW
    assert spend.params["reason"]
    assert set(spend.params["scores"]) == {
        "naive",
        "moving_average_7",
        "seasonal_naive",
        "seasonal_mean_4",
    }
    assert spend.params["n_folds"] >= 1
