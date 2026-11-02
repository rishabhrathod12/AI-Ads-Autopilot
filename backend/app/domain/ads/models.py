"""Advertising domain models. Provider-independent; every record keeps source + timestamp.

Money is integer paise (never floats). Derived KPIs (ACOS, ROAS, ...) are computed in M3,
not stored here.
"""

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Paise = Annotated[int, Field(ge=0)]
Count = Annotated[int, Field(ge=0)]
ExternalId = Annotated[str, Field(min_length=1, max_length=64)]


class DataSource(StrEnum):
    MOCK = "MOCK"
    AMAZON_ADS_API = "AMAZON_ADS_API"


class EntityType(StrEnum):
    CAMPAIGN = "campaign"
    AD_GROUP = "ad_group"
    TARGET = "target"
    SEARCH_TERM = "search_term"


class State(StrEnum):
    ENABLED = "enabled"
    PAUSED = "paused"
    ARCHIVED = "archived"


class TargetingType(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"


class MatchType(StrEnum):
    BROAD = "broad"
    PHRASE = "phrase"
    EXACT = "exact"
    AUTO = "auto"  # auto-targeting groups (close-match, loose-match, ...)


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    data_source: DataSource
    ingested_at: AwareDatetime


class Profile(_Record):
    external_id: ExternalId
    name: str
    marketplace: str  # e.g. "Amazon.in"
    country: str  # ISO 3166-1 alpha-2
    region: str  # Ads API region: NA / EU / FE
    currency: str = Field(min_length=3, max_length=3)
    timezone: str


class Campaign(_Record):
    external_id: ExternalId
    profile_id: ExternalId
    name: str
    state: State
    targeting_type: TargetingType
    daily_budget_paise: Paise
    bidding_strategy: str


class AdGroup(_Record):
    external_id: ExternalId
    campaign_id: ExternalId
    name: str
    default_bid_paise: Paise
    state: State


class Target(_Record):
    """A keyword or auto-targeting group."""

    external_id: ExternalId
    ad_group_id: ExternalId
    text: str
    match_type: MatchType
    bid_paise: Paise
    state: State
    is_negative: bool = False


class SearchTerm(_Record):
    external_id: ExternalId
    ad_group_id: ExternalId
    target_id: ExternalId | None
    term: str


class DailyMetrics(_Record):
    """One entity's metrics for one day (attribution by click date)."""

    entity_type: EntityType
    entity_id: ExternalId
    date: date
    impressions: Count
    clicks: Count
    spend_paise: Paise
    sales_paise: Paise  # advertising-attributed sales, NOT total business revenue
    orders: Count
    units: Count
    attribution_days: Annotated[int, Field(ge=1, le=30)]
    is_mature: bool  # False while the attribution window is still filling in

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.clicks > self.impressions:
            raise ValueError("clicks exceed impressions")
        if self.orders > self.clicks:
            raise ValueError("orders exceed clicks")
        if self.units < self.orders:
            raise ValueError("units below orders")
        if self.clicks == 0 and self.spend_paise != 0:
            raise ValueError("spend without clicks")
        if self.orders == 0 and (self.sales_paise != 0 or self.units != 0):
            raise ValueError("sales/units without orders")
        return self


class ChangeRequest(BaseModel):
    """A proposed change to a live advertising entity. Providers must never act on it unapproved."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_type: Literal[EntityType.CAMPAIGN, EntityType.TARGET]
    entity_id: ExternalId
    field: Literal["bid_paise", "daily_budget_paise"]
    new_value: Paise
