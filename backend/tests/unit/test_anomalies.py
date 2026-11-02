"""Anomaly detection service: structure, synthetic spike-vs-shift, and the default dataset."""

import math
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from app.application.analytics.anomalies import (
    AnomalyReport,
    Metric,
    Severity,
    Status,
    detect_anomalies,
)
from app.application.analytics.dashboard import NoDataError
from app.application.ports.performance_reader import EntityDailyPoint, Freshness
from app.domain.anomaly import Direction
from app.domain.kpi.metrics import MetricTotals
from app.domain.kpi.periods import DateRange
from app.infrastructure.providers.mock.generator import GeneratedData, MockConfig, generate
from tests.unit.fake_reader import FakeReader

END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
ORG = UUID("44444444-4444-4444-8444-444444444444")
MATURE_END = END - timedelta(days=3)


@pytest.fixture(scope="module")
def data() -> GeneratedData:
    return generate(MockConfig(end_date=END, now=NOW))


@pytest.fixture(scope="module")
def report(data: GeneratedData) -> AnomalyReport:
    import asyncio

    return asyncio.run(detect_anomalies(FakeReader(data, ORG, NOW)))


def test_windows_are_mature_only_and_adjacent(report: AnomalyReport) -> None:
    assert report.monitor == DateRange(MATURE_END - timedelta(days=13), MATURE_END)
    assert report.reference.days == 56
    assert report.reference.end == report.monitor.start - timedelta(days=1)
    assert report.provenance.as_of == NOW


def test_episode_fields_are_internally_consistent(report: AnomalyReport) -> None:
    assert report.episodes
    for e in report.episodes:
        assert report.monitor.contains(e.onset)
        assert e.onset <= e.detected_on <= e.last_day <= report.monitor.end
        assert (e.severity is Severity.ALERT) == (e.alert_on is not None)
        if e.alert_on is not None:
            assert e.detected_on <= e.alert_on <= e.last_day
        assert e.evidence > 0
        sign = 1 if e.direction is Direction.UP else -1
        assert sign * e.relative_change_pct > 0  # observed moved the way the episode claims


def test_planted_conversion_drop_is_seen_on_the_default_seed(
    report: AnomalyReport, data: GeneratedData
) -> None:
    """Sanity on seed 42 (inspected while building; not scored).

    Only one of the two planted drops is detected here, and that is the statistics, not a bug:
    the other campaign has ~41 clicks/day and, by chance, its conversion rate fell by 34% rather
    than the true 50% (evidence 3.4 vs a watch bar of 4.7). See the held-out evaluation for power.
    """
    cvr_down = [
        e for e in report.episodes if e.metric is Metric.CVR and e.direction is Direction.DOWN
    ]
    assert cvr_down
    assert {e.campaign.external_id for e in cvr_down} <= set(data.truth.anomaly_campaign_ids)
    assert all(e.status is Status.ONGOING for e in cvr_down)
    assert all(e.relative_change_pct < -25 for e in cvr_down)


def test_episodes_carry_the_acos_breakdown_for_context(
    report: AnomalyReport, data: GeneratedData
) -> None:
    by_id = {c.campaign.id: c.campaign.external_id for c in report.episodes}
    assert set(report.acos_context) == set(by_id)
    for cid, ctx in report.acos_context.items():
        if by_id[cid] in data.truth.anomaly_campaign_ids and ctx is not None:
            assert ctx.main_driver.name == "cvr"
            assert ctx.change_points > 0


def test_ordering_puts_alerts_first(report: AnomalyReport) -> None:
    flags = [e.severity is Severity.ALERT for e in report.episodes]
    assert flags == sorted(flags, reverse=True)


async def test_empty_account_raises_no_data(data: GeneratedData) -> None:
    class Empty(FakeReader):
        async def freshness(self) -> Freshness:
            return Freshness(None, None)

    with pytest.raises(NoDataError):
        await detect_anomalies(Empty(data, ORG, NOW))


async def test_campaigns_without_history_are_skipped_not_guessed(data: GeneratedData) -> None:
    class NoHistory(FakeReader):
        async def daily_by_entity(self, *a: object, **k: object) -> list[EntityDailyPoint]:
            return []

    r = await detect_anomalies(NoHistory(data, ORG, NOW))
    assert r.episodes == []
    assert {s.metric for s in r.skipped} == {Metric.CVR, Metric.CPC, Metric.SPEND}
    assert len(r.skipped) == 3 * len(data.campaigns)


# ---- synthetic series: a one-off promotion vs a lasting change -----------------------------
def synthetic_reader(data: GeneratedData, scale: Sequence[float]) -> FakeReader:
    """One campaign with steady, mildly wobbling traffic; ``scale`` multiplies demand per
    monitor day (clicks, orders and spend scale together, so CPC and CVR stay unchanged)."""

    class Synthetic(FakeReader):
        async def daily_by_entity(
            self, entity_type: object, period: DateRange, entity_ids: Sequence[UUID], **kw: object
        ) -> list[EntityDailyPoint]:
            camp = (await self.campaigns())[0].id
            out = []
            for i in range(period.days):
                day = period.start + timedelta(days=i)
                from_end = (MATURE_END - day).days
                factor = scale[13 - from_end] if 0 <= from_end <= 13 else 1.0
                wobble = 1 + 0.05 * math.sin(i * 1.7)
                clicks = round(60 * factor * wobble)
                cpc = 1300 * (
                    1 + 0.04 * math.sin(i * 2.3)
                )  # CPC wobbles a little, as real CPC does
                out.append(
                    EntityDailyPoint(
                        camp,
                        day,
                        MetricTotals(
                            clicks * 200, clicks, round(clicks * cpc), round(clicks * 6000),
                            round(clicks * 0.09), round(clicks * 0.09),
                        ),
                        True,
                    )
                )  # fmt: skip
            return out

    return Synthetic(data, ORG, NOW)


async def test_a_two_day_promotion_is_reported_as_subsided(data: GeneratedData) -> None:
    scale = [1.0] * 4 + [2.5, 2.5] + [1.0] * 8
    r = await detect_anomalies(synthetic_reader(data, scale))
    spend_up = [e for e in r.episodes if e.metric is Metric.SPEND and e.direction is Direction.UP]
    assert spend_up
    assert all(e.status is Status.SUBSIDED for e in spend_up)
    assert not [e for e in r.episodes if e.metric is Metric.CVR]  # conversion rate never moved


async def test_a_lasting_demand_increase_is_reported_as_ongoing(data: GeneratedData) -> None:
    scale = [1.0] * 6 + [1.6] * 8
    r = await detect_anomalies(synthetic_reader(data, scale))
    spend_up = [e for e in r.episodes if e.metric is Metric.SPEND and e.direction is Direction.UP]
    assert spend_up
    assert all(e.status is Status.ONGOING for e in spend_up)
    assert spend_up[0].relative_change_pct > 40
    assert not [e for e in r.episodes if e.metric is Metric.CPC]  # price per click unchanged


async def test_steady_traffic_raises_nothing(data: GeneratedData) -> None:
    r = await detect_anomalies(synthetic_reader(data, [1.0] * 14))
    assert r.episodes == []
    first = (await synthetic_reader(data, [1.0] * 14).campaigns())[0].external_id
    assert all(s.campaign_external_id != first for s in r.skipped)  # it was fully monitored
