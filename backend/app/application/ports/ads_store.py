"""Persistence port for synced advertising data. Bound to one tenant-scoped transaction."""

from collections.abc import Sequence
from datetime import date
from typing import Protocol
from uuid import UUID

from app.domain.ads.models import (
    AdGroup,
    Campaign,
    DailyMetrics,
    DataSource,
    Profile,
    SearchTerm,
    Target,
)


class AdsStore(Protocol):
    async def ensure_seller_account(
        self, *, provider_type: DataSource, marketplace: str, region: str
    ) -> UUID: ...

    async def upsert_profiles(self, seller_account_id: UUID, items: Sequence[Profile]) -> None: ...

    async def upsert_campaigns(self, items: Sequence[Campaign]) -> None: ...

    async def upsert_ad_groups(self, items: Sequence[AdGroup]) -> None: ...

    async def upsert_targets(self, items: Sequence[Target]) -> None: ...

    async def upsert_search_terms(self, items: Sequence[SearchTerm]) -> None: ...

    async def start_report_run(
        self, *, profile_id: str, report_type: str, start: date, end: date, source: DataSource
    ) -> UUID: ...

    async def upsert_metrics(self, run_id: UUID, items: Sequence[DailyMetrics]) -> None: ...

    async def finish_report_run(
        self, run_id: UUID, *, status: str, rows_ingested: int, rows_quarantined: int
    ) -> None: ...
