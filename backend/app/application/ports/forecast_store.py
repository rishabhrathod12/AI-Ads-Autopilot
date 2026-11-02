"""Persistence port for forecasts. Stored forecasts are immutable and cite their evidence."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from app.domain.ads.models import DataSource

ScopeType = Literal["account", "campaign"]
Metric = Literal["spend", "sales"]


@dataclass(frozen=True)
class NewForecastRun:
    scope_type: ScopeType
    scope_id: UUID | None  # campaign id; None for the account
    metric: Metric
    horizon_start: date
    horizon_end: date
    train_start: date
    train_end: date
    train_days: int
    model: str
    used_baseline: bool
    params: dict[str, Any]  # how the model was chosen: scores, folds, reason, coverage
    points: list[dict[str, Any]]  # [{date, point, lower, upper}] in paise
    data_source: DataSource
    input_as_of: datetime | None


@dataclass(frozen=True)
class StoredForecast(NewForecastRun):
    id: UUID
    created_at: datetime


class ForecastStore(Protocol):
    async def save(self, run: NewForecastRun) -> UUID: ...

    async def get(self, run_id: UUID) -> StoredForecast | None: ...

    async def latest(
        self, scope_type: ScopeType, scope_id: UUID | None, metric: Metric
    ) -> StoredForecast | None: ...
