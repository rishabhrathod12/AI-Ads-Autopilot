"""SyncService against an in-memory store, with faults injected into a real mock provider."""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.application.sync_service import SyncService
from app.domain.ads.models import (
    AdGroup,
    Campaign,
    DailyMetrics,
    DataSource,
    EntityType,
    Profile,
    SearchTerm,
    Target,
)
from app.infrastructure.providers.mock.generator import MockConfig
from app.infrastructure.providers.mock.provider import MockAdvertisingDataProvider

END = date(2026, 10, 31)
START = END - timedelta(days=29)
NOW = datetime(2026, 11, 1, tzinfo=UTC)


def small() -> MockConfig:
    return MockConfig(
        days=30,
        end_date=END,
        n_campaigns=6,
        ad_groups_per_campaign=2,
        keywords_per_ad_group=8,
        now=NOW,
    )


class FakeStore:
    def __init__(self) -> None:
        self.campaigns: list[str] = []
        self.ad_groups: list[str] = []
        self.targets: list[str] = []
        self.terms: list[str] = []
        self.metrics: list[DailyMetrics] = []
        self.runs: dict[UUID, dict[str, Any]] = {}

    async def ensure_seller_account(self, **_: Any) -> UUID:
        return uuid4()

    async def upsert_profiles(self, seller_account_id: UUID, items: Sequence[Profile]) -> None:
        pass

    async def upsert_campaigns(self, items: Sequence[Campaign]) -> None:
        self.campaigns += [i.external_id for i in items]

    async def upsert_ad_groups(self, items: Sequence[AdGroup]) -> None:
        self.ad_groups += [i.external_id for i in items]

    async def upsert_targets(self, items: Sequence[Target]) -> None:
        self.targets += [i.external_id for i in items]

    async def upsert_search_terms(self, items: Sequence[SearchTerm]) -> None:
        self.terms += [i.external_id for i in items]

    async def start_report_run(self, **kw: Any) -> UUID:
        run_id = uuid4()
        self.runs[run_id] = dict(kw)
        return run_id

    async def upsert_metrics(self, run_id: UUID, items: Sequence[DailyMetrics]) -> None:
        self.metrics += list(items)

    async def finish_report_run(self, run_id: UUID, **kw: Any) -> None:
        self.runs[run_id].update(kw)


async def run(provider: MockAdvertisingDataProvider) -> tuple[Any, FakeStore]:
    store = FakeStore()
    result = await SyncService(provider, store).sync(START, END)
    return result, store


async def test_clean_sync_persists_everything_with_no_quarantine() -> None:
    p = MockAdvertisingDataProvider(small())
    result, store = await run(p)
    d = p.data
    assert result.rows_quarantined == 0
    assert (result.campaigns, result.ad_groups) == (len(d.campaigns), len(d.ad_groups))
    assert (result.targets, result.search_terms) == (len(d.targets), len(d.search_terms))
    assert result.metrics_rows == sum(len(t.day) for t in d.metrics.values()) == len(store.metrics)
    assert len(store.runs) == 4  # one provenance row per entity type
    assert all(
        r["status"] == "completed" and r["source"] is DataSource.MOCK for r in store.runs.values()
    )


async def test_orphans_wrong_source_and_bad_references_are_quarantined() -> None:
    class Faulty(MockAdvertisingDataProvider):
        async def list_campaigns(self, profile_id: str) -> list[Campaign]:
            good = await super().list_campaigns(profile_id)
            base = good[0].model_dump()
            orphan = Campaign(**{**base, "external_id": "BAD-CMP-1", "profile_id": "NO-PROFILE"})
            foreign = Campaign(
                **{**base, "external_id": "BAD-CMP-2", "data_source": DataSource.AMAZON_ADS_API}
            )
            return [*good, orphan, foreign]

        async def list_ad_groups(self, profile_id: str) -> list[AdGroup]:
            good = await super().list_ad_groups(profile_id)
            base = good[0].model_dump()
            # parent is a campaign that was itself quarantined
            return [
                *good,
                AdGroup(**{**base, "external_id": "BAD-AG-1", "campaign_id": "BAD-CMP-1"}),
            ]

        async def list_search_terms(self, profile_id: str) -> list[SearchTerm]:
            good = await super().list_search_terms(profile_id)
            base = good[0].model_dump()
            ghost_target = SearchTerm(**{**base, "external_id": "BAD-ST-1", "target_id": "NO-TGT"})
            return [*good, ghost_target]

    p = Faulty(small())
    result, store = await run(p)
    reasons = {i.record_id: i.reason for i in result.quarantined}
    assert "unknown parent" in reasons["BAD-CMP-1"]
    assert "data_source" in reasons["BAD-CMP-2"]
    assert "unknown parent" in reasons["BAD-AG-1"]
    assert "unknown target" in reasons["BAD-ST-1"]
    for bad in ("BAD-CMP-1", "BAD-CMP-2", "BAD-AG-1", "BAD-ST-1"):
        assert bad not in store.campaigns + store.ad_groups + store.terms
    assert len(store.campaigns) == len(p.data.campaigns)  # good data unaffected


async def test_bad_metric_rows_are_quarantined_and_counted_on_the_run() -> None:
    class Faulty(MockAdvertisingDataProvider):
        async def fetch_daily_metrics(
            self, profile_id: str, entity_type: EntityType, start: date, end: date
        ) -> AsyncIterator[list[DailyMetrics]]:
            async for chunk in super().fetch_daily_metrics(profile_id, entity_type, start, end):
                if entity_type is EntityType.CAMPAIGN:
                    row = chunk[0]
                    unknown = row.model_copy(update={"entity_id": "GHOST"})
                    early = row.model_copy(update={"date": START - timedelta(days=1)})
                    wrong_source = row.model_copy(update={"data_source": DataSource.AMAZON_ADS_API})
                    chunk = [*chunk, row, unknown, early, wrong_source]  # `row` again = duplicate
                yield chunk

    result, store = await run(Faulty(small()))
    reasons = [i.reason for i in result.quarantined if i.record_type == "metrics"]
    assert any("unknown campaign entity" in r for r in reasons)
    assert any("outside report range" in r for r in reasons)
    assert any("duplicate" in r for r in reasons)
    assert any("data_source" in r for r in reasons)
    assert len(reasons) == 4
    assert "GHOST" not in {m.entity_id for m in store.metrics}
    campaign_runs = [r for r in store.runs.values() if r["report_type"] == "daily_campaign"]
    assert campaign_runs[0]["rows_quarantined"] == 4
    assert campaign_runs[0]["status"] == "completed_with_quarantine"


async def test_rejects_inverted_date_range() -> None:
    with pytest.raises(ValueError):
        await SyncService(MockAdvertisingDataProvider(small()), FakeStore()).sync(END, START)
