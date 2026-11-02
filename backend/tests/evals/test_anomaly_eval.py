"""Scores anomaly detection against the planted conversion-rate drop.

PRE-REGISTERED before any detector existed, on seeds 11-15 (never used for anything else):

  * detected at watch level or better by the last mature day: >= 8 of 10 cases
    (2 planted campaigns x 5 seeds)
  * detected at alert level: >= 5 of 10
  * median watch-level detection delay from the true onset: <= 9 days
  * false-positive episodes per seed on the non-planted campaigns, all metrics and directions:
    mean <= 1.0 at alert level, mean <= 3.0 at watch level (alert episodes included)

The M3 plan said "within 5 days". That was written without a power analysis and is statistically
out of reach at ~40-60 clicks a day per campaign (a halved rate gives z of about 2 after 3 days
at that volume), so it was replaced by the criteria above before building.

The thresholds were calibrated by simulating NO change (calibrate*.py, not the generator), so no
planted scenario influenced them. These scores validate the implementation against the generator
that created the scenario; they do not measure accuracy on real accounts.
"""

import asyncio
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from app.application.analytics.anomalies import Metric, Severity, detect_anomalies
from app.domain.anomaly import Direction
from app.infrastructure.providers.mock.generator import MockConfig, generate
from tests.unit.fake_reader import FakeReader

pytestmark = pytest.mark.eval

END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, tzinfo=UTC)
ORG = UUID(int=11)
SEEDS = [11, 12, 13, 14, 15]

MIN_DETECTED = 8
MIN_ALERTED = 5
MAX_MEDIAN_DELAY_DAYS = 9
MAX_MEAN_FALSE_ALERTS = 1.0
MAX_MEAN_FALSE_EPISODES = 3.0


@dataclass(frozen=True)
class Outcome:
    seed: int
    campaign: str
    detected: bool
    alerted: bool
    delay_days: int | None  # watch-level detection day minus the true onset


@dataclass(frozen=True)
class SeedFalsePositives:
    seed: int
    alerts: int
    episodes: int


async def run_seed(seed: int) -> tuple[list[Outcome], SeedFalsePositives]:
    data = generate(MockConfig(seed=seed, end_date=END, now=NOW))
    report = await detect_anomalies(FakeReader(data, ORG, NOW))
    planted = data.truth.anomaly_campaign_ids
    outcomes = []
    for cid in sorted(planted):
        hits = [
            e
            for e in report.episodes
            if e.campaign.external_id == cid
            and e.metric is Metric.CVR
            and e.direction is Direction.DOWN
        ]
        first = min((e.detected_on for e in hits), default=None)
        outcomes.append(
            Outcome(
                seed,
                cid,
                detected=bool(hits),
                alerted=any(e.severity is Severity.ALERT for e in hits),
                delay_days=(first - data.truth.anomaly_start).days if first else None,
            )
        )
    other = [e for e in report.episodes if e.campaign.external_id not in planted]
    return outcomes, SeedFalsePositives(
        seed, sum(e.severity is Severity.ALERT for e in other), len(other)
    )


@pytest.fixture(scope="module")
def results() -> tuple[list[Outcome], list[SeedFalsePositives]]:
    async def run_all() -> tuple[list[Outcome], list[SeedFalsePositives]]:
        outcomes: list[Outcome] = []
        fps: list[SeedFalsePositives] = []
        for seed in SEEDS:
            o, f = await run_seed(seed)
            outcomes += o
            fps.append(f)
        return outcomes, fps

    return asyncio.run(run_all())


def test_report_results(results: tuple[list[Outcome], list[SeedFalsePositives]]) -> None:
    """Run with ``pytest -s -m eval`` to print every case for the docs."""
    outcomes, fps = results
    for o in outcomes:
        print(o)
    for f in fps:
        print(f)
    assert len(outcomes) == 2 * len(SEEDS)


def test_planted_drop_is_detected(results: tuple[list[Outcome], list[SeedFalsePositives]]) -> None:
    outcomes, _ = results
    assert sum(o.detected for o in outcomes) >= MIN_DETECTED
    assert sum(o.alerted for o in outcomes) >= MIN_ALERTED


def test_detection_delay_is_reasonable(
    results: tuple[list[Outcome], list[SeedFalsePositives]],
) -> None:
    outcomes, _ = results
    delays = [o.delay_days for o in outcomes if o.delay_days is not None]
    assert delays
    assert statistics.median(delays) <= MAX_MEDIAN_DELAY_DAYS


def test_false_positives_are_rare(
    results: tuple[list[Outcome], list[SeedFalsePositives]],
) -> None:
    _, fps = results
    assert statistics.fmean(f.alerts for f in fps) <= MAX_MEAN_FALSE_ALERTS
    assert statistics.fmean(f.episodes for f in fps) <= MAX_MEAN_FALSE_EPISODES
