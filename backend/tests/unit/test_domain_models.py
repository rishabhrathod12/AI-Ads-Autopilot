from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.ads.ids import entity_uuid
from app.domain.ads.models import ChangeRequest, DailyMetrics, DataSource, EntityType

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def metrics(**over: Any) -> DailyMetrics:
    base: dict[str, Any] = {
        "data_source": DataSource.MOCK,
        "ingested_at": NOW,
        "entity_type": EntityType.CAMPAIGN,
        "entity_id": "c1",
        "date": date(2026, 10, 1),
        "impressions": 1000,
        "clicks": 10,
        "spend_paise": 12_000,
        "sales_paise": 90_000,
        "orders": 2,
        "units": 2,
        "attribution_days": 7,
        "is_mature": True,
    }
    return DailyMetrics(**{**base, **over})


def test_valid_metrics_are_accepted() -> None:
    assert metrics().clicks == 10
    assert metrics(clicks=0, spend_paise=0, orders=0, units=0, sales_paise=0).clicks == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"clicks": 2000},  # clicks > impressions
        {"orders": 11, "units": 11},  # orders > clicks
        {"units": 1},  # units < orders
        {"clicks": 0, "orders": 0, "units": 0, "sales_paise": 0},  # spend without clicks
        {"orders": 0, "units": 0},  # sales without orders
        {"spend_paise": -1},
        {"impressions": -5},
        {"ingested_at": datetime(2026, 11, 1)},  # naive timestamp
    ],
)
def test_inconsistent_metrics_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        metrics(**bad)


def test_records_are_immutable() -> None:
    with pytest.raises(ValidationError):
        metrics().clicks = 5  # type: ignore[misc]


def test_change_request_rejects_unsupported_fields() -> None:
    ok: dict[str, Any] = {"entity_type": EntityType.TARGET, "entity_id": "t1"}
    ChangeRequest(**ok, field="bid_paise", new_value=1500)
    with pytest.raises(ValidationError):  # search terms cannot be changed directly
        ChangeRequest(
            entity_type=EntityType.SEARCH_TERM,
            entity_id="s1",
            **{"field": "bid_paise", "new_value": 1},
        )
    with pytest.raises(ValidationError):  # only bids and budgets are supported
        ChangeRequest(**ok, **{"field": "state", "new_value": 1})


def test_entity_uuid_is_deterministic_and_tenant_scoped() -> None:
    org_a, org_b = uuid4(), uuid4()
    assert entity_uuid(org_a, "campaign", "c1") == entity_uuid(org_a, "campaign", "c1")
    assert entity_uuid(org_a, "campaign", "c1") != entity_uuid(org_b, "campaign", "c1")
    assert entity_uuid(org_a, "campaign", "c1") != entity_uuid(org_a, "target", "c1")
