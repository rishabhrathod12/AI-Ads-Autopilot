"""Select the advertising data provider from configuration (ADS_PROVIDER)."""

from datetime import date

from app.application.ports.ads_provider import AdvertisingDataProvider, ProviderNotAvailableError
from app.config import Settings
from app.infrastructure.providers.mock.generator import MockConfig
from app.infrastructure.providers.mock.provider import MockAdvertisingDataProvider


def build_provider(settings: Settings, *, end_date: date | None = None) -> AdvertisingDataProvider:
    if settings.ads_provider == "mock":
        seed, days = settings.mock_seed, settings.mock_history_days
        if end_date is None:
            return MockAdvertisingDataProvider(MockConfig(seed=seed, days=days))
        return MockAdvertisingDataProvider(MockConfig(seed=seed, days=days, end_date=end_date))
    # Deliberately no fake: real Amazon connectivity arrives in M9, after API access is granted.
    raise ProviderNotAvailableError(
        "ADS_PROVIDER='amazon' is not implemented until M9 (Amazon Ads API access required)"
    )
