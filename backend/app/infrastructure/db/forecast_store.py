"""SQL ForecastStore bound to one tenant-scoped session."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.forecast_store import (
    Metric,
    NewForecastRun,
    ScopeType,
    StoredForecast,
)
from app.domain.ads.models import DataSource
from app.infrastructure.db.forecast_models import ForecastRun


def _stored(row: ForecastRun) -> StoredForecast:
    return StoredForecast(
        id=row.id,
        created_at=row.created_at,
        scope_type=row.scope_type,  # type: ignore[arg-type]
        scope_id=row.scope_id,
        metric=row.metric,  # type: ignore[arg-type]
        horizon_start=row.horizon_start,
        horizon_end=row.horizon_end,
        train_start=row.train_start,
        train_end=row.train_end,
        train_days=row.train_days,
        model=row.model,
        used_baseline=row.used_baseline,
        params=row.params,
        points=row.points,
        data_source=DataSource(row.data_source),
        input_as_of=row.input_as_of,
    )


class SqlForecastStore:
    def __init__(self, session: AsyncSession, organization_id: UUID) -> None:
        self._s = session
        self._org = organization_id

    async def save(self, run: NewForecastRun) -> UUID:
        run_id = uuid4()
        self._s.add(
            ForecastRun(
                id=run_id,
                organization_id=self._org,
                scope_type=run.scope_type,
                scope_id=run.scope_id,
                metric=run.metric,
                horizon_start=run.horizon_start,
                horizon_end=run.horizon_end,
                train_start=run.train_start,
                train_end=run.train_end,
                train_days=run.train_days,
                model=run.model,
                used_baseline=run.used_baseline,
                params=run.params,
                points=run.points,
                data_source=run.data_source.value,
                input_as_of=run.input_as_of,
            )
        )
        await self._s.flush()
        return run_id

    async def get(self, run_id: UUID) -> StoredForecast | None:
        row = await self._s.scalar(
            select(ForecastRun).where(
                ForecastRun.id == run_id, ForecastRun.organization_id == self._org
            )
        )
        return _stored(row) if row else None

    async def latest(
        self, scope_type: ScopeType, scope_id: UUID | None, metric: Metric
    ) -> StoredForecast | None:
        scope = (
            ForecastRun.scope_id.is_(None) if scope_id is None else ForecastRun.scope_id == scope_id
        )
        row = await self._s.scalar(
            select(ForecastRun)
            .where(
                ForecastRun.organization_id == self._org,
                ForecastRun.scope_type == scope_type,
                ForecastRun.metric == metric,
                scope,
            )
            .order_by(ForecastRun.created_at.desc(), ForecastRun.id)
            .limit(1)
        )
        return _stored(row) if row else None
