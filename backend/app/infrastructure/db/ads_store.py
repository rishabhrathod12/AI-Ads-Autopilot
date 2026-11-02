"""SQLAlchemy implementation of AdsStore, bound to one tenant-scoped session."""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.domain.ads.ids import entity_uuid
from app.domain.ads.models import (
    AdGroup,
    Campaign,
    DailyMetrics,
    DataSource,
    Profile,
    SearchTerm,
    Target,
)
from app.infrastructure.db import ads_models as m

MAX_BIND_PARAMS = 30_000  # asyncpg limit is 32767
_METRIC_KEY = ["organization_id", "entity_type", "entity_id", "date"]
_METRIC_COLUMNS = [
    *_METRIC_KEY,
    "impressions",
    "clicks",
    "spend_paise",
    "sales_paise",
    "orders",
    "units",
    "attribution_days",
    "is_mature",
    "report_run_id",
    "data_source",
    "ingested_at",
]


class SqlAdsStore:
    def __init__(self, session: AsyncSession, organization_id: UUID) -> None:
        self._s = session
        self._org = organization_id

    def _id(self, kind: str, external_id: str) -> UUID:
        return entity_uuid(self._org, kind, external_id)

    async def _upsert(
        self,
        model: type[DeclarativeBase],
        rows: list[dict[str, Any]],
        conflict: list[str],
        immutable: set[str],
    ) -> None:
        if not rows:
            return
        step = max(1, MAX_BIND_PARAMS // len(rows[0]))
        for i in range(0, len(rows), step):
            stmt = insert(model).values(rows[i : i + step])
            updates = {c: stmt.excluded[c] for c in rows[0] if c not in immutable}
            await self._s.execute(stmt.on_conflict_do_update(index_elements=conflict, set_=updates))

    async def ensure_seller_account(
        self, *, provider_type: DataSource, marketplace: str, region: str
    ) -> UUID:
        sid = self._id("seller_account", f"{provider_type.value}:{marketplace}")
        row = {
            "id": sid,
            "organization_id": self._org,
            "marketplace": marketplace,
            "region": region,
            "provider_type": provider_type.value,
        }
        await self._upsert(
            m.SellerAccount, [row], ["id"], {"id", "organization_id", "provider_type"}
        )
        return sid

    async def upsert_profiles(self, seller_account_id: UUID, items: Sequence[Profile]) -> None:
        rows = [
            {
                "id": self._id("profile", p.external_id),
                "organization_id": self._org,
                "seller_account_id": seller_account_id,
                "external_id": p.external_id,
                "name": p.name,
                "country": p.country,
                "currency": p.currency,
                "timezone": p.timezone,
                "data_source": p.data_source.value,
                "ingested_at": p.ingested_at,
            }
            for p in items
        ]
        await self._upsert(
            m.AdvertisingProfile, rows, ["id"], {"id", "organization_id", "external_id"}
        )

    async def upsert_campaigns(self, items: Sequence[Campaign]) -> None:
        rows = [
            {
                "id": self._id("campaign", c.external_id),
                "organization_id": self._org,
                "profile_id": self._id("profile", c.profile_id),
                "external_id": c.external_id,
                "name": c.name,
                "state": c.state.value,
                "targeting_type": c.targeting_type.value,
                "daily_budget_paise": c.daily_budget_paise,
                "bidding_strategy": c.bidding_strategy,
                "data_source": c.data_source.value,
                "ingested_at": c.ingested_at,
            }
            for c in items
        ]
        await self._upsert(m.Campaign, rows, ["id"], {"id", "organization_id", "external_id"})

    async def upsert_ad_groups(self, items: Sequence[AdGroup]) -> None:
        rows = [
            {
                "id": self._id("ad_group", a.external_id),
                "organization_id": self._org,
                "campaign_id": self._id("campaign", a.campaign_id),
                "external_id": a.external_id,
                "name": a.name,
                "default_bid_paise": a.default_bid_paise,
                "state": a.state.value,
                "data_source": a.data_source.value,
                "ingested_at": a.ingested_at,
            }
            for a in items
        ]
        await self._upsert(m.AdGroup, rows, ["id"], {"id", "organization_id", "external_id"})

    async def upsert_targets(self, items: Sequence[Target]) -> None:
        rows = [
            {
                "id": self._id("target", t.external_id),
                "organization_id": self._org,
                "ad_group_id": self._id("ad_group", t.ad_group_id),
                "external_id": t.external_id,
                "text": t.text,
                "match_type": t.match_type.value,
                "bid_paise": t.bid_paise,
                "state": t.state.value,
                "is_negative": t.is_negative,
                "data_source": t.data_source.value,
                "ingested_at": t.ingested_at,
            }
            for t in items
        ]
        await self._upsert(m.KeywordTarget, rows, ["id"], {"id", "organization_id", "external_id"})

    async def upsert_search_terms(self, items: Sequence[SearchTerm]) -> None:
        rows = [
            {
                "id": self._id("search_term", s.external_id),
                "organization_id": self._org,
                "ad_group_id": self._id("ad_group", s.ad_group_id),
                "target_id": self._id("target", s.target_id) if s.target_id else None,
                "external_id": s.external_id,
                "term": s.term,
                "data_source": s.data_source.value,
                "ingested_at": s.ingested_at,
            }
            for s in items
        ]
        await self._upsert(m.SearchTermRow, rows, ["id"], {"id", "organization_id", "external_id"})

    async def start_report_run(
        self, *, profile_id: str, report_type: str, start: date, end: date, source: DataSource
    ) -> UUID:
        run_id = uuid4()
        self._s.add(
            m.ReportRun(
                id=run_id,
                organization_id=self._org,
                profile_id=self._id("profile", profile_id),
                report_type=report_type,
                date_start=start,
                date_end=end,
                status="running",
                data_source=source.value,
                ingested_at=datetime.now(UTC),
            )
        )
        await self._s.flush()
        return run_id

    async def upsert_metrics(self, run_id: UUID, items: Sequence[DailyMetrics]) -> None:
        """Bulk upsert via COPY into a staging table (orders of magnitude faster than VALUES).

        Runs on the session's own connection, so the tenant setting, ``SET LOCAL ROLE`` and RLS
        policies all still apply to the final INSERT.
        """
        if not items:
            return
        records = [
            (
                self._org,
                d.entity_type.value,
                self._id(d.entity_type.value, d.entity_id),
                d.date,
                d.impressions,
                d.clicks,
                d.spend_paise,
                d.sales_paise,
                d.orders,
                d.units,
                d.attribution_days,
                d.is_mature,
                run_id,
                d.data_source.value,
                d.ingested_at,
            )
            for d in items
        ]
        cols = _METRIC_COLUMNS
        col_list = ", ".join(cols)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in _METRIC_KEY)
        conn = await self._s.connection()
        driver: Any = (await conn.get_raw_connection()).driver_connection
        await driver.execute(
            "CREATE TEMP TABLE _stg_metrics (LIKE performance_daily INCLUDING DEFAULTS)"
        )
        try:
            await driver.copy_records_to_table("_stg_metrics", records=records, columns=cols)
            await driver.execute(
                f"INSERT INTO performance_daily ({col_list}) SELECT {col_list} FROM _stg_metrics "  # noqa: S608
                f"ON CONFLICT ({', '.join(_METRIC_KEY)}) DO UPDATE SET {updates}"
            )
        finally:
            await driver.execute("DROP TABLE IF EXISTS _stg_metrics")

    async def finish_report_run(
        self, run_id: UUID, *, status: str, rows_ingested: int, rows_quarantined: int
    ) -> None:
        await self._s.execute(
            update(m.ReportRun)
            .where(m.ReportRun.id == run_id)
            .values(status=status, rows_ingested=rows_ingested, rows_quarantined=rows_quarantined)
        )
