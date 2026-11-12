"""AmazonAdsApiProvider: the shape of the real provider, with NO endpoint mapping.

Everything that depends on Amazon's actual API (endpoint paths, report definitions, field names,
header scheme) could not be verified from the official documentation, so none of it is guessed here.
Reads raise ``ProviderNotAvailableError`` naming what is missing; ``execute_change`` ALWAYS raises
``ExecutionNotPermittedError``: executing approved changes needs its own design, ADR and approval
(see docs/AMAZON_ADS_INTEGRATION.md). This class is deliberately not wired into ``build_provider``,
so ``ADS_PROVIDER=amazon`` still fails to start the sync path.
"""

from collections.abc import AsyncIterator
from datetime import date

from app.application.ports.ads_provider import (
    ExecutionNotPermittedError,
    ProviderNotAvailableError,
)
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
from app.infrastructure.ads_api.http_client import AdsHttpClient

_MISSING = (
    "the Amazon Ads endpoint mapping is not implemented: it must be written from the verified "
    "official documentation (see docs/AMAZON_ADS_INTEGRATION.md)"
)


class AmazonAdsApiProvider:
    def __init__(self, client: AdsHttpClient) -> None:
        self._client = client

    @property
    def source(self) -> DataSource:
        return DataSource.AMAZON_ADS_API

    async def list_profiles(self) -> list[Profile]:
        raise ProviderNotAvailableError(_MISSING)

    async def list_campaigns(self, profile_id: str) -> list[Campaign]:
        raise ProviderNotAvailableError(_MISSING)

    async def list_ad_groups(self, profile_id: str) -> list[AdGroup]:
        raise ProviderNotAvailableError(_MISSING)

    async def list_targets(self, profile_id: str) -> list[Target]:
        raise ProviderNotAvailableError(_MISSING)

    async def list_search_terms(self, profile_id: str) -> list[SearchTerm]:
        raise ProviderNotAvailableError(_MISSING)

    async def fetch_daily_metrics(
        self, profile_id: str, entity_type: EntityType, start: date, end: date
    ) -> AsyncIterator[list[DailyMetrics]]:
        raise ProviderNotAvailableError(_MISSING)
        yield []  # pragma: no cover - makes this an async generator, as the port requires

    async def execute_change(self, profile_id: str, change: ChangeRequest) -> None:
        raise ExecutionNotPermittedError(
            "executing changes on Amazon Ads is not implemented and not permitted in this build"
        )
