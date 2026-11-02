"""SQL PerformanceReader. Bound to a tenant-scoped session; RLS applies on top of the explicit
organization filter used here (which also lets the planner use the primary-key prefix)."""

from collections.abc import Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.base import Executable

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
from app.domain.ads.models import DataSource, EntityType
from app.domain.kpi.metrics import MetricTotals
from app.domain.kpi.periods import DateRange
from app.infrastructure.db import ads_models as m

P = m.PerformanceDaily


def _sums() -> list[Any]:
    """SUM() columns in MetricTotals field order."""
    return [
        func.sum(c)
        for c in (P.impressions, P.clicks, P.spend_paise, P.sales_paise, P.orders, P.units)
    ]


def _totals(values: Sequence[Any]) -> MetricTotals:
    return MetricTotals(*(int(v) for v in values))  # SUM(int) arrives as Decimal


class SqlPerformanceReader:
    def __init__(self, session: AsyncSession, organization_id: UUID) -> None:
        self._s = session
        self._org = organization_id

    def _where(
        self,
        entity_type: EntityType,
        period: DateRange,
        include_immature: bool,
        entity_ids: Sequence[UUID] | None,
    ) -> list[ColumnElement[bool]]:
        conds = [
            P.organization_id == self._org,
            P.entity_type == entity_type.value,
            P.date.between(period.start, period.end),
        ]
        if not include_immature:
            conds.append(P.is_mature.is_(True))
        if entity_ids is not None:
            conds.append(P.entity_id.in_(entity_ids))
        return conds

    async def _rows(self, stmt: Executable) -> list[Any]:
        return list((await self._s.execute(stmt)).all())

    async def freshness(self) -> Freshness:
        stmt = select(func.max(P.date), func.min(P.date).filter(P.is_mature.is_(False))).where(
            P.organization_id == self._org, P.entity_type == EntityType.CAMPAIGN.value
        )
        latest, first_immature = (await self._rows(stmt))[0]
        if latest is None:
            return Freshness(None, None)
        mature = first_immature - timedelta(days=1) if first_immature is not None else latest
        return Freshness(latest, mature)

    async def provenance(self, period: DateRange) -> Provenance:
        stmt = (
            select(P.data_source, func.max(P.ingested_at))
            .where(*self._where(EntityType.CAMPAIGN, period, True, None))
            .group_by(P.data_source)
        )
        rows = await self._rows(stmt)
        return Provenance(
            data_sources=frozenset(DataSource(r[0]) for r in rows),
            as_of=max((r[1] for r in rows), default=None),
        )

    async def campaigns(self) -> list[CampaignInfo]:
        stmt = (
            select(m.Campaign)
            .where(m.Campaign.organization_id == self._org)
            .order_by(m.Campaign.external_id)
        )
        return [
            CampaignInfo(
                id=c.id,
                external_id=c.external_id,
                name=c.name,
                state=c.state,
                targeting_type=c.targeting_type,
                daily_budget_paise=c.daily_budget_paise,
                data_source=DataSource(c.data_source),
            )
            for c in (await self._s.scalars(stmt)).all()
        ]

    async def targets(self) -> list[TargetInfo]:
        t, g = m.KeywordTarget, m.AdGroup
        stmt = (
            select(t, g.campaign_id)
            .join(g, g.id == t.ad_group_id)
            .where(t.organization_id == self._org)
            .order_by(t.external_id)
        )
        return [
            TargetInfo(
                id=row.id,
                external_id=row.external_id,
                ad_group_id=row.ad_group_id,
                campaign_id=campaign_id,
                text=row.text,
                match_type=row.match_type,
                bid_paise=row.bid_paise,
                state=row.state,
                is_negative=row.is_negative,
            )
            for row, campaign_id in (await self._s.execute(stmt)).all()
        ]

    async def search_terms(self) -> list[SearchTermInfo]:
        s_, g = m.SearchTermRow, m.AdGroup
        stmt = (
            select(s_, g.campaign_id)
            .join(g, g.id == s_.ad_group_id)
            .where(s_.organization_id == self._org)
            .order_by(s_.external_id)
        )
        return [
            SearchTermInfo(
                id=row.id,
                external_id=row.external_id,
                ad_group_id=row.ad_group_id,
                campaign_id=campaign_id,
                target_id=row.target_id,
                term=row.term,
            )
            for row, campaign_id in (await self._s.execute(stmt)).all()
        ]

    async def daily_totals(
        self,
        entity_type: EntityType,
        period: DateRange,
        *,
        include_immature: bool = True,
        entity_ids: Sequence[UUID] | None = None,
    ) -> list[DailyPoint]:
        if entity_ids is not None and not entity_ids:
            return []
        stmt = (
            select(P.date, *_sums(), func.bool_and(P.is_mature))
            .where(*self._where(entity_type, period, include_immature, entity_ids))
            .group_by(P.date)
            .order_by(P.date)
        )
        return [DailyPoint(r[0], _totals(r[1:7]), bool(r[7])) for r in await self._rows(stmt)]

    async def totals_by_entity(
        self,
        entity_type: EntityType,
        period: DateRange,
        *,
        include_immature: bool = True,
        entity_ids: Sequence[UUID] | None = None,
    ) -> list[EntityTotals]:
        if entity_ids is not None and not entity_ids:
            return []
        stmt = (
            select(
                P.entity_id,
                *_sums(),
                func.count(),
                func.count().filter(P.is_mature.is_(False)),
            )
            .where(*self._where(entity_type, period, include_immature, entity_ids))
            .group_by(P.entity_id)
            .order_by(P.entity_id)
        )
        return [
            EntityTotals(r[0], _totals(r[1:7]), int(r[7]), int(r[8]))
            for r in await self._rows(stmt)
        ]

    async def daily_by_entity(
        self,
        entity_type: EntityType,
        period: DateRange,
        entity_ids: Sequence[UUID],
        *,
        include_immature: bool = True,
    ) -> list[EntityDailyPoint]:
        if not entity_ids:
            return []
        stmt = (
            select(
                P.entity_id,
                P.date,
                P.impressions,
                P.clicks,
                P.spend_paise,
                P.sales_paise,
                P.orders,
                P.units,
                P.is_mature,
            )
            .where(*self._where(entity_type, period, include_immature, entity_ids))
            .order_by(P.entity_id, P.date)
        )
        return [
            EntityDailyPoint(r[0], r[1], _totals(r[2:8]), bool(r[8]))
            for r in await self._rows(stmt)
        ]
