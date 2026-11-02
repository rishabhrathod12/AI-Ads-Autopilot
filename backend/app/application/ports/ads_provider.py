"""AdvertisingDataProvider port: what agents and services depend on (never a concrete provider)."""

from collections.abc import AsyncIterator
from datetime import date
from typing import Protocol

from app.domain.ads.models import (
    AdGroup,
    Campaign,
    ChangeRequest,
    DailyMetrics,
    DataSource,
    EntityType,
    Profile,
    SearchTerm,
    Target,
)


class ExecutionNotPermittedError(RuntimeError):
    """Raised when a provider is asked to change live advertising and must refuse."""


class ProviderNotAvailableError(RuntimeError):
    """Raised when a configured provider has no implementation yet."""


class AdvertisingDataProvider(Protocol):
    """Methods taking ``profile_id`` raise ``KeyError`` for a profile the provider does not know."""

    @property
    def source(self) -> DataSource:
        """Origin label stamped on every record this provider returns."""
        ...

    async def list_profiles(self) -> list[Profile]: ...

    async def list_campaigns(self, profile_id: str) -> list[Campaign]: ...

    async def list_ad_groups(self, profile_id: str) -> list[AdGroup]: ...

    async def list_targets(self, profile_id: str) -> list[Target]: ...

    async def list_search_terms(self, profile_id: str) -> list[SearchTerm]: ...

    def fetch_daily_metrics(
        self, profile_id: str, entity_type: EntityType, start: date, end: date
    ) -> AsyncIterator[list[DailyMetrics]]:
        """Stream daily metrics for [start, end] inclusive, in chunks (reports can be large)."""
        ...

    async def execute_change(self, profile_id: str, change: ChangeRequest) -> None:
        """Apply an approved change. Mock providers must always raise ExecutionNotPermittedError."""
        ...
