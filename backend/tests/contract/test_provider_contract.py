"""Contract every AdvertisingDataProvider must satisfy. Add real providers to PROVIDERS in M9."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest

from app.application.ports.ads_provider import AdvertisingDataProvider
from app.domain.ads.models import DailyMetrics, DataSource, EntityType
from app.infrastructure.providers.mock.generator import MockConfig
from app.infrastructure.providers.mock.provider import MockAdvertisingDataProvider

END = date(2026, 10, 31)


def _mock() -> AdvertisingDataProvider:
    cfg = MockConfig(
        days=35,
        end_date=END,
        n_campaigns=6,
        ad_groups_per_campaign=2,
        keywords_per_ad_group=8,
        now=datetime(2026, 11, 1, tzinfo=UTC),
    )
    return MockAdvertisingDataProvider(cfg)


PROVIDERS: dict[str, Callable[[], AdvertisingDataProvider]] = {"mock": _mock}


@pytest.fixture(params=PROVIDERS)
def provider(request: pytest.FixtureRequest) -> AdvertisingDataProvider:
    return PROVIDERS[request.param]()


async def collect(
    p: AdvertisingDataProvider, pid: str, etype: EntityType, start: date, end: date
) -> list[DailyMetrics]:
    rows: list[DailyMetrics] = []
    async for chunk in p.fetch_daily_metrics(pid, etype, start, end):
        assert chunk, "providers must not yield empty chunks"
        rows.extend(chunk)
    return rows


async def test_records_carry_provider_source_and_aware_timestamps(
    provider: AdvertisingDataProvider,
) -> None:
    (profile,) = await provider.list_profiles()
    pid = profile.external_id
    records = [
        profile,
        *await provider.list_campaigns(pid),
        *await provider.list_ad_groups(pid),
        *await provider.list_targets(pid),
        *await provider.list_search_terms(pid),
    ]
    assert isinstance(provider.source, DataSource)
    for r in records:
        assert r.data_source == provider.source
        assert r.ingested_at.tzinfo is not None


async def test_hierarchy_is_referentially_consistent(provider: AdvertisingDataProvider) -> None:
    (profile,) = await provider.list_profiles()
    pid = profile.external_id
    campaigns = await provider.list_campaigns(pid)
    groups = await provider.list_ad_groups(pid)
    targets = await provider.list_targets(pid)
    terms = await provider.list_search_terms(pid)
    assert campaigns
    assert all(c.profile_id == pid for c in campaigns)
    camp_ids = {c.external_id for c in campaigns}
    group_ids = {g.external_id for g in groups}
    target_ids = {t.external_id for t in targets}
    assert all(g.campaign_id in camp_ids for g in groups)
    assert all(t.ad_group_id in group_ids for t in targets)
    assert all(s.ad_group_id in group_ids for s in terms)
    assert all(s.target_id is None or s.target_id in target_ids for s in terms)


@pytest.mark.parametrize("etype", list(EntityType))
async def test_metrics_are_well_formed_and_known(
    provider: AdvertisingDataProvider, etype: EntityType
) -> None:
    (profile,) = await provider.list_profiles()
    pid = profile.external_id
    known = {
        EntityType.CAMPAIGN: {c.external_id for c in await provider.list_campaigns(pid)},
        EntityType.AD_GROUP: {g.external_id for g in await provider.list_ad_groups(pid)},
        EntityType.TARGET: {t.external_id for t in await provider.list_targets(pid)},
        EntityType.SEARCH_TERM: {s.external_id for s in await provider.list_search_terms(pid)},
    }[etype]
    rows = await collect(provider, pid, etype, END - timedelta(days=34), END)
    assert rows
    keys = [(r.entity_type, r.entity_id, r.date) for r in rows]
    assert len(keys) == len(set(keys)), "duplicate (entity, date) rows"
    assert all(r.entity_type == etype and r.entity_id in known for r in rows)
    assert all(r.data_source == provider.source and r.ingested_at.tzinfo for r in rows)


async def test_date_range_is_respected(provider: AdvertisingDataProvider) -> None:
    (profile,) = await provider.list_profiles()
    start, end = END - timedelta(days=20), END - timedelta(days=10)
    rows = await collect(provider, profile.external_id, EntityType.CAMPAIGN, start, end)
    assert rows
    assert min(r.date for r in rows) >= start
    assert max(r.date for r in rows) <= end


async def test_unknown_profile_is_rejected(provider: AdvertisingDataProvider) -> None:
    with pytest.raises(KeyError):
        await provider.list_campaigns("does-not-exist")


async def test_recent_days_are_flagged_immature(provider: AdvertisingDataProvider) -> None:
    (profile,) = await provider.list_profiles()
    rows = await collect(
        provider, profile.external_id, EntityType.CAMPAIGN, END - timedelta(days=10), END
    )
    latest = [r for r in rows if r.date == END]
    older = [r for r in rows if r.date == END - timedelta(days=10)]
    assert latest and all(not r.is_mature for r in latest)
    assert older and all(r.is_mature for r in older)
