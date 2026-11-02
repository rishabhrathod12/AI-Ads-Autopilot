"""Pulls advertising data from a provider and persists it with provenance.

Records that fail validation are quarantined: reported in the result, counted on the report run,
and never persisted or silently "fixed".
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

import structlog

from app.application.ports.ads_provider import AdvertisingDataProvider
from app.application.ports.ads_store import AdsStore
from app.domain.ads.models import DataSource, EntityType

log = structlog.get_logger()

_ENTITY_ORDER = (
    EntityType.CAMPAIGN,
    EntityType.AD_GROUP,
    EntityType.TARGET,
    EntityType.SEARCH_TERM,
)


@dataclass(frozen=True)
class SyncIssue:
    record_type: str
    record_id: str
    reason: str


@dataclass
class SyncResult:
    profiles: int = 0
    campaigns: int = 0
    ad_groups: int = 0
    targets: int = 0
    search_terms: int = 0
    metrics_rows: int = 0
    quarantined: list[SyncIssue] = field(default_factory=list)

    @property
    def rows_quarantined(self) -> int:
        return len(self.quarantined)


class SyncService:
    def __init__(self, provider: AdvertisingDataProvider, store: AdsStore) -> None:
        self._provider = provider
        self._store = store

    def _source_ok(
        self, record_type: str, record_id: str, source: DataSource, out: SyncResult
    ) -> bool:
        if source != self._provider.source:
            out.quarantined.append(
                SyncIssue(
                    record_type,
                    record_id,
                    f"data_source {source} != provider {self._provider.source}",
                )
            )
            return False
        return True

    def _require_parent(
        self,
        items: Iterable[tuple[str, str, DataSource]],
        known_parents: set[str],
        record_type: str,
        out: SyncResult,
    ) -> set[str]:
        """items: (id, parent_id, source). Returns accepted ids; quarantines the rest."""
        accepted: set[str] = set()
        for rid, parent, source in items:
            if not self._source_ok(record_type, rid, source, out):
                continue
            if parent not in known_parents:
                out.quarantined.append(SyncIssue(record_type, rid, f"unknown parent {parent}"))
                continue
            accepted.add(rid)
        return accepted

    async def sync(self, start: date, end: date) -> SyncResult:
        if end < start:
            raise ValueError("end must not be before start")
        out = SyncResult()
        provider = self._provider
        for profile in await provider.list_profiles():
            if not self._source_ok("profile", profile.external_id, profile.data_source, out):
                continue
            pid = profile.external_id
            seller = await self._store.ensure_seller_account(
                provider_type=provider.source,
                marketplace=profile.marketplace,
                region=profile.region,
            )
            await self._store.upsert_profiles(seller, [profile])
            out.profiles += 1

            campaigns = await provider.list_campaigns(pid)
            ok = self._require_parent(
                ((c.external_id, c.profile_id, c.data_source) for c in campaigns),
                {pid},
                "campaign",
                out,
            )
            await self._store.upsert_campaigns([c for c in campaigns if c.external_id in ok])
            known = {EntityType.CAMPAIGN: ok}
            out.campaigns += len(ok)

            groups = await provider.list_ad_groups(pid)
            ok = self._require_parent(
                ((a.external_id, a.campaign_id, a.data_source) for a in groups),
                known[EntityType.CAMPAIGN],
                "ad_group",
                out,
            )
            await self._store.upsert_ad_groups([a for a in groups if a.external_id in ok])
            known[EntityType.AD_GROUP] = ok
            out.ad_groups += len(ok)

            targets = await provider.list_targets(pid)
            ok = self._require_parent(
                ((t.external_id, t.ad_group_id, t.data_source) for t in targets),
                known[EntityType.AD_GROUP],
                "target",
                out,
            )
            await self._store.upsert_targets([t for t in targets if t.external_id in ok])
            known[EntityType.TARGET] = ok
            out.targets += len(ok)

            terms = await provider.list_search_terms(pid)
            ok = self._require_parent(
                ((s.external_id, s.ad_group_id, s.data_source) for s in terms),
                known[EntityType.AD_GROUP],
                "search_term",
                out,
            )
            # A term pointing at an unknown target would violate the FK: quarantine it too.
            by_id = {s.external_id: s for s in terms}
            for rid in list(ok):
                tid = by_id[rid].target_id
                if tid is not None and tid not in known[EntityType.TARGET]:
                    ok.discard(rid)
                    out.quarantined.append(SyncIssue("search_term", rid, f"unknown target {tid}"))
            await self._store.upsert_search_terms([s for s in terms if s.external_id in ok])
            known[EntityType.SEARCH_TERM] = ok
            out.search_terms += len(ok)

            for etype in _ENTITY_ORDER:
                await self._sync_metrics(pid, etype, known[etype], start, end, out)
        log.info(
            "sync_complete",
            profiles=out.profiles,
            metrics_rows=out.metrics_rows,
            quarantined=out.rows_quarantined,
            source=provider.source.value,
        )
        return out

    async def _sync_metrics(
        self,
        profile_id: str,
        etype: EntityType,
        known: set[str],
        start: date,
        end: date,
        out: SyncResult,
    ) -> None:
        run_id = await self._store.start_report_run(
            profile_id=profile_id,
            report_type=f"daily_{etype.value}",
            start=start,
            end=end,
            source=self._provider.source,
        )
        ingested = quarantined = 0
        async for chunk in self._provider.fetch_daily_metrics(profile_id, etype, start, end):
            good = {}
            for row in chunk:
                key = (row.entity_id, row.date)
                if not self._source_ok("metrics", row.entity_id, row.data_source, out):
                    quarantined += 1
                elif row.entity_type != etype or row.entity_id not in known:
                    out.quarantined.append(
                        SyncIssue("metrics", row.entity_id, f"unknown {etype.value} entity")
                    )
                    quarantined += 1
                elif not start <= row.date <= end:
                    out.quarantined.append(
                        SyncIssue("metrics", row.entity_id, f"date {row.date} outside report range")
                    )
                    quarantined += 1
                elif key in good:
                    out.quarantined.append(
                        SyncIssue("metrics", row.entity_id, f"duplicate row for {row.date}")
                    )
                    quarantined += 1
                else:
                    good[key] = row
            await self._store.upsert_metrics(run_id, list(good.values()))
            ingested += len(good)
        out.metrics_rows += ingested
        await self._store.finish_report_run(
            run_id,
            status="completed_with_quarantine" if quarantined else "completed",
            rows_ingested=ingested,
            rows_quarantined=quarantined,
        )
