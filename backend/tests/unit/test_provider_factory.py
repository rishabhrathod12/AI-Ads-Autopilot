from datetime import date

import pytest

from app.application.ports.ads_provider import (
    ExecutionNotPermittedError,
    ProviderNotAvailableError,
)
from app.config import Settings
from app.domain.ads.models import ChangeRequest, EntityType
from app.infrastructure.providers.factory import build_provider
from app.infrastructure.providers.mock.provider import MockAdvertisingDataProvider


def test_mock_is_selected_by_default() -> None:
    p = build_provider(Settings(_env_file=None), end_date=date(2026, 10, 31))
    assert isinstance(p, MockAdvertisingDataProvider)
    assert p.source.value == "MOCK"


def test_seed_and_history_come_from_settings() -> None:
    p = build_provider(
        Settings(mock_seed=5, mock_history_days=45, _env_file=None), end_date=date(2026, 10, 31)
    )
    assert isinstance(p, MockAdvertisingDataProvider)
    assert len(p.data.dates) == 45


def test_default_history_is_120_days() -> None:
    assert Settings(_env_file=None).mock_history_days == 120


def test_amazon_provider_is_explicitly_not_available() -> None:
    with pytest.raises(ProviderNotAvailableError, match="M9"):
        build_provider(Settings(ads_provider="amazon", _env_file=None))


async def test_mock_provider_never_executes_changes() -> None:
    p = build_provider(Settings(_env_file=None), end_date=date(2026, 10, 31))
    change = ChangeRequest(
        entity_type=EntityType.TARGET, entity_id="MOCK-TGT-00000", field="bid_paise", new_value=900
    )
    with pytest.raises(ExecutionNotPermittedError):
        await p.execute_change("MOCK-PRF-001", change)
