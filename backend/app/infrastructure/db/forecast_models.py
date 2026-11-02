"""ORM model for stored forecasts (immutable: a re-forecast is a new row)."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.models import Base


class ForecastRun(Base):
    __tablename__ = "forecast_runs"
    __table_args__ = (
        CheckConstraint("scope_type IN ('account', 'campaign')"),
        CheckConstraint("metric IN ('spend', 'sales')"),
        CheckConstraint("data_source IN ('MOCK', 'AMAZON_ADS_API')"),
        CheckConstraint(
            "(scope_type = 'account' AND scope_id IS NULL) "
            "OR (scope_type = 'campaign' AND scope_id IS NOT NULL)"
        ),
        CheckConstraint("horizon_end >= horizon_start AND train_end >= train_start"),
    )
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    metric: Mapped[str] = mapped_column(String(20), nullable=False)
    horizon_start: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_end: Mapped[date] = mapped_column(Date, nullable=False)
    train_start: Mapped[date] = mapped_column(Date, nullable=False)
    train_end: Mapped[date] = mapped_column(Date, nullable=False)
    train_days: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    used_baseline: Mapped[bool] = mapped_column(Boolean, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    points: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    data_source: Mapped[str] = mapped_column(String(30), nullable=False)
    input_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # clock_timestamp(), not now(): now() is the transaction start, so rows saved in one
    # transaction would tie and "latest" would be arbitrary.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )
