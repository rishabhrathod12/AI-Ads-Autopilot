"""MockAdvertisingDataProvider: serves synthetic data. Never executes real actions."""

from collections.abc import AsyncIterator
from datetime import date

from app.application.ports.ads_provider import ExecutionNotPermittedError
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
from app.infrastructure.providers.mock.generator import (
    GeneratedData,
    GroundTruth,
    MockConfig,
    generate,
)

CHUNK = 5_000


class MockAdvertisingDataProvider:
    def __init__(self, config: MockConfig | None = None) -> None:
        self._config = config or MockConfig()
        self._data: GeneratedData | None = None

    @property
    def source(self) -> DataSource:
        return DataSource.MOCK

    @property
    def data(self) -> GeneratedData:
        if self._data is None:
            self._data = generate(self._config)
        return self._data

    @property
    def ground_truth(self) -> GroundTruth:
        """Planted scenarios for evaluating analytics. Mock-only; not part of the port."""
        return self.data.truth

    def _check_profile(self, profile_id: str) -> None:
        if profile_id != self.data.profile.external_id:
            raise KeyError(f"unknown profile: {profile_id}")

    async def list_profiles(self) -> list[Profile]:
        return [self.data.profile]

    async def list_campaigns(self, profile_id: str) -> list[Campaign]:
        self._check_profile(profile_id)
        return list(self.data.campaigns)

    async def list_ad_groups(self, profile_id: str) -> list[AdGroup]:
        self._check_profile(profile_id)
        return list(self.data.ad_groups)

    async def list_targets(self, profile_id: str) -> list[Target]:
        self._check_profile(profile_id)
        return list(self.data.targets)

    async def list_search_terms(self, profile_id: str) -> list[SearchTerm]:
        self._check_profile(profile_id)
        return list(self.data.search_terms)

    async def fetch_daily_metrics(
        self, profile_id: str, entity_type: EntityType, start: date, end: date
    ) -> AsyncIterator[list[DailyMetrics]]:
        self._check_profile(profile_id)
        data = self.data
        table = data.metrics[entity_type.value]
        first = max(0, (start - data.dates[0]).days)
        last = min(len(data.dates) - 1, (end - data.dates[0]).days)
        keep = (table.day >= first) & (table.day <= last)
        rows = keep.nonzero()[0]
        for lo in range(0, len(rows), CHUNK):
            sel = rows[lo : lo + CHUNK]
            cols = {
                name: getattr(table, name)[sel].tolist()
                for name in (
                    "impressions",
                    "clicks",
                    "spend_paise",
                    "sales_paise",
                    "orders",
                    "units",
                )
            }
            entity, day = table.entity[sel].tolist(), table.day[sel].tolist()
            yield [
                DailyMetrics(
                    data_source=DataSource.MOCK,
                    ingested_at=self._config.now,
                    entity_type=entity_type,
                    entity_id=table.ids[entity[i]],
                    date=data.dates[day[i]],
                    attribution_days=data.attribution_days,
                    is_mature=day[i] <= data.mature_through,
                    impressions=cols["impressions"][i],
                    clicks=cols["clicks"][i],
                    spend_paise=cols["spend_paise"][i],
                    sales_paise=cols["sales_paise"][i],
                    orders=cols["orders"][i],
                    units=cols["units"][i],
                )
                for i in range(len(sel))
            ]

    async def execute_change(self, profile_id: str, change: ChangeRequest) -> None:
        raise ExecutionNotPermittedError(
            "MockAdvertisingDataProvider never executes real advertising changes"
        )
