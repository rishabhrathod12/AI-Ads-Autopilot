"""ORM models for synced advertising data. All tenant-scoped (RLS) and provenance-stamped."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.models import Base

_SOURCES = "data_source IN ('MOCK', 'AMAZON_ADS_API')"


def _org() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)


def _fk(table: str, nullable: bool = False) -> Mapped[uuid.UUID]:
    return mapped_column(PG_UUID(as_uuid=True), ForeignKey(f"{table}.id"), nullable=nullable)


def _prov() -> tuple[Mapped[str], Mapped[datetime]]:
    return (
        mapped_column(String(30), nullable=False),
        mapped_column(DateTime(timezone=True), nullable=False),
    )


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True)


class SellerAccount(Base):
    __tablename__ = "seller_accounts"
    __table_args__ = (CheckConstraint(_SOURCES),)
    id: Mapped[uuid.UUID] = _pk()
    organization_id: Mapped[uuid.UUID] = _org()
    marketplace: Mapped[str] = mapped_column(String(50), nullable=False)
    region: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    provider_type: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = _created()


class AdvertisingProfile(Base):
    __tablename__ = "advertising_profiles"
    __table_args__ = (UniqueConstraint("organization_id", "external_id"), CheckConstraint(_SOURCES))
    id: Mapped[uuid.UUID] = _pk()
    organization_id: Mapped[uuid.UUID] = _org()
    seller_account_id: Mapped[uuid.UUID] = _fk("seller_accounts")
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    data_source: Mapped[str] = _prov()[0]
    ingested_at: Mapped[datetime] = _prov()[1]
    created_at: Mapped[datetime] = _created()


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (UniqueConstraint("organization_id", "external_id"), CheckConstraint(_SOURCES))
    id: Mapped[uuid.UUID] = _pk()
    organization_id: Mapped[uuid.UUID] = _org()
    profile_id: Mapped[uuid.UUID] = _fk("advertising_profiles")
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    targeting_type: Mapped[str] = mapped_column(String(20), nullable=False)
    daily_budget_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bidding_strategy: Mapped[str] = mapped_column(String(100), nullable=False)
    data_source: Mapped[str] = _prov()[0]
    ingested_at: Mapped[datetime] = _prov()[1]
    created_at: Mapped[datetime] = _created()


class AdGroup(Base):
    __tablename__ = "ad_groups"
    __table_args__ = (UniqueConstraint("organization_id", "external_id"), CheckConstraint(_SOURCES))
    id: Mapped[uuid.UUID] = _pk()
    organization_id: Mapped[uuid.UUID] = _org()
    campaign_id: Mapped[uuid.UUID] = _fk("campaigns")
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    default_bid_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    data_source: Mapped[str] = _prov()[0]
    ingested_at: Mapped[datetime] = _prov()[1]
    created_at: Mapped[datetime] = _created()


class KeywordTarget(Base):
    __tablename__ = "keywords_targets"
    __table_args__ = (UniqueConstraint("organization_id", "external_id"), CheckConstraint(_SOURCES))
    id: Mapped[uuid.UUID] = _pk()
    organization_id: Mapped[uuid.UUID] = _org()
    ad_group_id: Mapped[uuid.UUID] = _fk("ad_groups")
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    match_type: Mapped[str] = mapped_column(String(20), nullable=False)
    bid_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    is_negative: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    data_source: Mapped[str] = _prov()[0]
    ingested_at: Mapped[datetime] = _prov()[1]
    created_at: Mapped[datetime] = _created()


class SearchTermRow(Base):
    __tablename__ = "search_terms"
    __table_args__ = (UniqueConstraint("organization_id", "external_id"), CheckConstraint(_SOURCES))
    id: Mapped[uuid.UUID] = _pk()
    organization_id: Mapped[uuid.UUID] = _org()
    ad_group_id: Mapped[uuid.UUID] = _fk("ad_groups")
    target_id: Mapped[uuid.UUID | None] = _fk("keywords_targets", nullable=True)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    term: Mapped[str] = mapped_column(String(500), nullable=False)
    data_source: Mapped[str] = _prov()[0]
    ingested_at: Mapped[datetime] = _prov()[1]
    created_at: Mapped[datetime] = _created()


class ReportRun(Base):
    """Provenance anchor: one row per ingested report."""

    __tablename__ = "report_runs"
    __table_args__ = (CheckConstraint(_SOURCES),)
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = _org()
    profile_id: Mapped[uuid.UUID] = _fk("advertising_profiles")
    report_type: Mapped[str] = mapped_column(String(50), nullable=False)
    date_start: Mapped[date] = mapped_column(Date, nullable=False)
    date_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    rows_ingested: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rows_quarantined: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    data_source: Mapped[str] = _prov()[0]
    ingested_at: Mapped[datetime] = _prov()[1]
    created_at: Mapped[datetime] = _created()


class PerformanceDaily(Base):
    __tablename__ = "performance_daily"
    __table_args__ = (
        PrimaryKeyConstraint("organization_id", "entity_type", "entity_id", "date"),
        CheckConstraint(_SOURCES),
        CheckConstraint("clicks <= impressions AND orders <= clicks AND units >= orders"),
        CheckConstraint(
            "impressions >= 0 AND clicks >= 0 AND spend_paise >= 0 AND sales_paise >= 0"
        ),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    date: Mapped[date] = mapped_column(Date)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False)
    spend_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sales_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    orders: Mapped[int] = mapped_column(Integer, nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    attribution_days: Mapped[int] = mapped_column(Integer, nullable=False)
    is_mature: Mapped[bool] = mapped_column(Boolean, nullable=False)
    report_run_id: Mapped[uuid.UUID] = _fk("report_runs")
    data_source: Mapped[str] = _prov()[0]
    ingested_at: Mapped[datetime] = _prov()[1]
