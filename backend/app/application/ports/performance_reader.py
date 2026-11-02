"""Read-side port for analytics: aggregated performance, scoped to one tenant.

Aggregation happens in the data store (callers never pull raw rows for dashboards). Results are
additive ``MetricTotals``; KPIs are derived from them with ``app.domain.kpi`` (aggregate, then
ratio). ``include_immature=False`` drops days still inside the attribution window.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from app.domain.ads.models import DataSource, EntityType
from app.domain.kpi.metrics import MetricTotals
from app.domain.kpi.periods import DateRange


@dataclass(frozen=True)
class DailyPoint:
    day: date
    totals: MetricTotals
    is_mature: bool


@dataclass(frozen=True)
class EntityTotals:
    entity_id: UUID
    totals: MetricTotals
    active_days: int  # days with any traffic
    immature_days: int  # of those, days still inside the attribution window


@dataclass(frozen=True)
class EntityDailyPoint:
    entity_id: UUID
    day: date
    totals: MetricTotals
    is_mature: bool


@dataclass(frozen=True)
class Freshness:
    latest_date: date | None
    mature_through: date | None  # last day whose attribution window is complete


@dataclass(frozen=True)
class Provenance:
    """Where the numbers came from; every API response must carry this."""

    data_sources: frozenset[DataSource]
    as_of: datetime | None  # most recent ingestion timestamp in scope


@dataclass(frozen=True)
class CampaignInfo:
    id: UUID
    external_id: str
    name: str
    state: str
    targeting_type: str
    daily_budget_paise: int
    data_source: DataSource


@dataclass(frozen=True)
class TargetInfo:
    """A keyword or auto-targeting group, with its place in the hierarchy."""

    id: UUID
    external_id: str
    ad_group_id: UUID
    campaign_id: UUID
    text: str
    match_type: str
    bid_paise: int
    state: str
    is_negative: bool


@dataclass(frozen=True)
class SearchTermInfo:
    id: UUID
    external_id: str
    ad_group_id: UUID
    campaign_id: UUID
    target_id: UUID | None
    term: str


class PerformanceReader(Protocol):
    async def freshness(self) -> Freshness: ...

    async def provenance(self, period: DateRange) -> Provenance: ...

    async def campaigns(self) -> list[CampaignInfo]: ...

    async def targets(self) -> list[TargetInfo]: ...

    async def search_terms(self) -> list[SearchTermInfo]: ...

    async def daily_totals(
        self,
        entity_type: EntityType,
        period: DateRange,
        *,
        include_immature: bool = True,
        entity_ids: Sequence[UUID] | None = None,
    ) -> list[DailyPoint]:
        """Totals per day over one hierarchy level (all entities, or only ``entity_ids``)."""
        ...

    async def totals_by_entity(
        self,
        entity_type: EntityType,
        period: DateRange,
        *,
        include_immature: bool = True,
        entity_ids: Sequence[UUID] | None = None,
    ) -> list[EntityTotals]:
        """Period totals per entity, ordered by entity id."""
        ...

    async def daily_by_entity(
        self,
        entity_type: EntityType,
        period: DateRange,
        entity_ids: Sequence[UUID],
        *,
        include_immature: bool = True,
    ) -> list[EntityDailyPoint]:
        """Daily series per entity. ``entity_ids`` is required to bound the result size."""
        ...
