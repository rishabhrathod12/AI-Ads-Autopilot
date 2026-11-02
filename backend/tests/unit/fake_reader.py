"""In-memory PerformanceReader built from the mock generator's numpy tables.

Independent of SQL on purpose: the integration test asserts the SQL reader and this one agree.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

import numpy as np

from app.application.ports.performance_reader import (
    CampaignInfo,
    DailyPoint,
    EntityDailyPoint,
    EntityTotals,
    Freshness,
    Provenance,
    SearchTermInfo,
    TargetInfo,
)
from app.domain.ads.ids import entity_uuid
from app.domain.ads.models import DataSource, EntityType
from app.domain.kpi.metrics import MetricTotals
from app.domain.kpi.periods import DateRange
from app.infrastructure.providers.mock.generator import GeneratedData, MetricsTable

FIELDS = ("impressions", "clicks", "spend_paise", "sales_paise", "orders", "units")


class FakeReader:
    def __init__(self, data: GeneratedData, org: UUID, now: datetime) -> None:
        self.data, self.org, self._now = data, org, now

    def _mask(
        self,
        t: MetricsTable,
        period: DateRange,
        include_immature: bool,
        ids: set[str] | None,
    ) -> np.ndarray:
        first = (period.start - self.data.dates[0]).days
        last = (period.end - self.data.dates[0]).days
        m = (t.day >= first) & (t.day <= last)
        if not include_immature:
            m &= t.day <= self.data.mature_through
        if ids is not None:
            keep = np.array([eid in ids for eid in t.ids])
            m &= keep[t.entity]
        return m

    def _ext_ids(self, etype: EntityType, ids: Sequence[UUID] | None) -> set[str] | None:
        if ids is None:
            return None
        wanted = set(ids)
        return {
            e
            for e in self.data.metrics[etype.value].ids
            if entity_uuid(self.org, etype.value, e) in wanted
        }

    async def freshness(self) -> Freshness:
        return Freshness(self.data.dates[-1], self.data.dates[self.data.mature_through])

    async def provenance(self, period: DateRange) -> Provenance:
        return Provenance(frozenset({DataSource.MOCK}), self._now)

    async def campaigns(self) -> list[CampaignInfo]:
        return sorted(
            (
                CampaignInfo(
                    id=entity_uuid(self.org, "campaign", c.external_id),
                    external_id=c.external_id,
                    name=c.name,
                    state=c.state.value,
                    targeting_type=c.targeting_type.value,
                    daily_budget_paise=c.daily_budget_paise,
                    data_source=DataSource.MOCK,
                )
                for c in self.data.campaigns
            ),
            key=lambda c: c.external_id,
        )

    def _campaign_of_ad_group(self) -> dict[str, str]:
        return {g.external_id: g.campaign_id for g in self.data.ad_groups}

    async def targets(self) -> list[TargetInfo]:
        camp = self._campaign_of_ad_group()
        u = self.org
        return sorted(
            (
                TargetInfo(
                    id=entity_uuid(u, "target", t.external_id),
                    external_id=t.external_id,
                    ad_group_id=entity_uuid(u, "ad_group", t.ad_group_id),
                    campaign_id=entity_uuid(u, "campaign", camp[t.ad_group_id]),
                    text=t.text,
                    match_type=t.match_type.value,
                    bid_paise=t.bid_paise,
                    state=t.state.value,
                    is_negative=t.is_negative,
                )
                for t in self.data.targets
            ),
            key=lambda x: x.external_id,
        )

    async def search_terms(self) -> list[SearchTermInfo]:
        camp = self._campaign_of_ad_group()
        u = self.org
        return sorted(
            (
                SearchTermInfo(
                    id=entity_uuid(u, "search_term", s.external_id),
                    external_id=s.external_id,
                    ad_group_id=entity_uuid(u, "ad_group", s.ad_group_id),
                    campaign_id=entity_uuid(u, "campaign", camp[s.ad_group_id]),
                    target_id=entity_uuid(u, "target", s.target_id) if s.target_id else None,
                    term=s.term,
                )
                for s in self.data.search_terms
            ),
            key=lambda x: x.external_id,
        )

    async def daily_totals(
        self,
        entity_type: EntityType,
        period: DateRange,
        *,
        include_immature: bool = True,
        entity_ids: Sequence[UUID] | None = None,
    ) -> list[DailyPoint]:
        t = self.data.metrics[entity_type.value]
        m = self._mask(t, period, include_immature, self._ext_ids(entity_type, entity_ids))
        out = []
        for day in sorted({int(d) for d in t.day[m]}):
            sel = m & (t.day == day)
            out.append(
                DailyPoint(
                    self.data.dates[day],
                    MetricTotals(*(int(getattr(t, f)[sel].sum()) for f in FIELDS)),
                    day <= self.data.mature_through,
                )
            )
        return out

    async def totals_by_entity(
        self,
        entity_type: EntityType,
        period: DateRange,
        *,
        include_immature: bool = True,
        entity_ids: Sequence[UUID] | None = None,
    ) -> list[EntityTotals]:
        t = self.data.metrics[entity_type.value]
        m = self._mask(t, period, include_immature, self._ext_ids(entity_type, entity_ids))
        n = len(t.ids)
        ent = t.entity[m]
        active = np.bincount(ent, minlength=n)
        immature = np.bincount(ent[t.day[m] > self.data.mature_through], minlength=n)
        sums = {
            f: np.rint(np.bincount(ent, weights=getattr(t, f)[m], minlength=n)).astype(np.int64)
            for f in FIELDS
        }
        out = [
            EntityTotals(
                entity_uuid(self.org, entity_type.value, t.ids[i]),
                MetricTotals(*(int(sums[f][i]) for f in FIELDS)),
                int(active[i]),
                int(immature[i]),
            )
            for i in np.nonzero(active)[0]
        ]
        return sorted(out, key=lambda e: e.entity_id)

    async def daily_by_entity(
        self,
        entity_type: EntityType,
        period: DateRange,
        entity_ids: Sequence[UUID],
        *,
        include_immature: bool = True,
    ) -> list[EntityDailyPoint]:
        t = self.data.metrics[entity_type.value]
        m = self._mask(t, period, include_immature, self._ext_ids(entity_type, entity_ids))
        rows = []
        for idx in np.nonzero(m)[0]:
            day = int(t.day[idx])
            rows.append(
                EntityDailyPoint(
                    entity_uuid(self.org, entity_type.value, t.ids[int(t.entity[idx])]),
                    self.data.dates[day],
                    MetricTotals(*(int(getattr(t, f)[idx]) for f in FIELDS)),
                    day <= self.data.mature_through,
                )
            )
        return sorted(rows, key=lambda p: (p.entity_id, p.day))
