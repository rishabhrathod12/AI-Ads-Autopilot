"""Campaign anomaly detection: sustained shifts in conversion rate, CPC and spend.

Judged on mature days only. The most recent ``ANOMALY_MONITOR_DAYS`` are compared with the
``ANOMALY_REFERENCE_DAYS`` before them. Findings are *shifts with evidence*, not causes: each
episode with a shift also carries the arithmetic ACOS breakdown for context ("which factor moved").

Honest limits: a single campaign has modest traffic, so a halving of its conversion rate takes
about a week to confirm at "watch" level and a few days longer at "alert" level. Faster detection
would mean accepting many false alarms.
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from uuid import UUID

from app.application.analytics.dashboard import NoDataError
from app.application.analytics.thresholds import (
    ANOMALY_MIN_CLICKS_FOR_CPC_DAY,
    ANOMALY_MIN_REFERENCE_CLICKS,
    ANOMALY_MIN_REFERENCE_DAYS,
    ANOMALY_MONITOR_DAYS,
    ANOMALY_REFERENCE_DAYS,
    CVR_H_ALERT,
    CVR_H_WATCH,
    CVR_RATIO_DOWN,
    CVR_RATIO_UP,
    GAUSS_H_ALERT,
    GAUSS_H_WATCH,
    GAUSS_K,
)
from app.application.ports.performance_reader import (
    CampaignInfo,
    Freshness,
    PerformanceReader,
    Provenance,
)
from app.domain.ads.models import EntityType
from app.domain.anomaly import (
    Direction,
    Run,
    Trace,
    bernoulli_trace,
    find_runs,
    gaussian_trace,
    robust_center_scale,
)
from app.domain.kpi.explain import AcosExplanation, explain_acos_change
from app.domain.kpi.metrics import MetricTotals
from app.domain.kpi.periods import DateRange


class Metric(StrEnum):
    CVR = "cvr"
    CPC = "cpc"
    SPEND = "spend"


class Severity(StrEnum):
    ALERT = "alert"  # strict: rarely a false alarm
    WATCH = "watch"  # earlier, but expect some false alarms


class Status(StrEnum):
    ONGOING = "ongoing"  # still adding evidence over the last days
    SUBSIDED = "subsided"  # moved, then came back (e.g. a one-off promotion)


@dataclass(frozen=True)
class Episode:
    campaign: CampaignInfo
    metric: Metric
    direction: Direction
    severity: Severity
    status: Status
    onset: date  # estimated first day of the change
    detected_on: date  # first day the watch bar was crossed
    alert_on: date | None  # first day the alert bar was crossed
    last_day: date
    reference_value: float  # CVR as a fraction; CPC and spend in paise
    observed_value: float  # same unit, over the episode days
    relative_change_pct: float
    evidence: float  # peak CUSUM statistic


@dataclass(frozen=True)
class Skipped:
    campaign_external_id: str
    metric: Metric
    reason: str


@dataclass(frozen=True)
class AnomalyReport:
    monitor: DateRange
    reference: DateRange
    episodes: list[Episode]
    skipped: list[Skipped]
    acos_context: dict[UUID, AcosExplanation | None]  # campaigns with a shift: window vs reference
    freshness: Freshness
    provenance: Provenance


def _days(period: DateRange) -> list[date]:
    return [period.start + timedelta(days=i) for i in range(period.days)]


def _total(series: dict[date, MetricTotals], days: list[date]) -> MetricTotals:
    return MetricTotals.sum(series.get(d, MetricTotals()) for d in days)


def _ratio(num: float, den: float) -> float:
    return (num / den - 1.0) * 100.0 if den > 0 else 0.0


async def detect_anomalies(
    reader: PerformanceReader,
    *,
    monitor_days: int = ANOMALY_MONITOR_DAYS,
    reference_days: int = ANOMALY_REFERENCE_DAYS,
) -> AnomalyReport:
    fresh = await reader.freshness()
    if fresh.latest_date is None or fresh.mature_through is None:
        raise NoDataError("no advertising data")
    end = fresh.mature_through
    monitor = DateRange(end - timedelta(days=monitor_days - 1), end)
    reference = DateRange(
        monitor.start - timedelta(days=reference_days), monitor.start - timedelta(days=1)
    )
    mon_days, ref_days = _days(monitor), _days(reference)

    campaigns = await reader.campaigns()
    by_campaign: dict[UUID, dict[date, MetricTotals]] = defaultdict(dict)
    for p in await reader.daily_by_entity(
        EntityType.CAMPAIGN,
        DateRange(reference.start, end),
        [c.id for c in campaigns],
        include_immature=False,
    ):
        by_campaign[p.entity_id][p.day] = p.totals

    episodes: list[Episode] = []
    skipped: list[Skipped] = []
    context: dict[UUID, AcosExplanation | None] = {}
    for c in campaigns:
        series = by_campaign.get(c.id, {})
        found = _cvr(c, series, mon_days, ref_days, skipped)
        found += _gaussian(c, series, mon_days, ref_days, Metric.CPC, skipped)
        found += _gaussian(c, series, mon_days, ref_days, Metric.SPEND, skipped)
        episodes += found
        if found:
            first = min(e.onset for e in found)
            window = [d for d in mon_days if d >= first]
            context[c.id] = explain_acos_change(_total(series, window), _total(series, ref_days))

    episodes.sort(key=lambda e: (e.severity is not Severity.ALERT, e.detected_on, -e.evidence))
    return AnomalyReport(
        monitor=monitor,
        reference=reference,
        episodes=episodes,
        skipped=skipped,
        acos_context=context,
        freshness=fresh,
        provenance=await reader.provenance(monitor),
    )


def _episode(
    c: CampaignInfo,
    metric: Metric,
    direction: Direction,
    run: Run,
    days: list[date],
    reference_value: float,
    observed_value: float,
) -> Episode:
    return Episode(
        campaign=c,
        metric=metric,
        direction=direction,
        severity=Severity.ALERT if run.first_alert is not None else Severity.WATCH,
        status=Status.ONGOING if run.ongoing else Status.SUBSIDED,
        onset=days[run.start],
        detected_on=days[run.first_watch],
        alert_on=days[run.first_alert] if run.first_alert is not None else None,
        last_day=days[run.end],
        reference_value=reference_value,
        observed_value=observed_value,
        relative_change_pct=_ratio(observed_value, reference_value),
        evidence=run.peak,
    )


def _cvr(
    c: CampaignInfo,
    series: dict[date, MetricTotals],
    mon_days: list[date],
    ref_days: list[date],
    skipped: list[Skipped],
) -> list[Episode]:
    ref = _total(series, ref_days)
    if ref.clicks < ANOMALY_MIN_REFERENCE_CLICKS or ref.orders == 0:
        skipped.append(Skipped(c.external_id, Metric.CVR, "not enough reference clicks/orders"))
        return []
    p0 = ref.orders / ref.clicks
    if not 0.0 < p0 < 1.0:
        skipped.append(Skipped(c.external_id, Metric.CVR, "degenerate reference rate"))
        return []
    orders = [series.get(d, MetricTotals()).orders for d in mon_days]
    clicks = [series.get(d, MetricTotals()).clicks for d in mon_days]
    out: list[Episode] = []
    for direction, ratio in ((Direction.DOWN, CVR_RATIO_DOWN), (Direction.UP, CVR_RATIO_UP)):
        trace = bernoulli_trace(orders, clicks, p0, direction, ratio)
        for run in find_runs(trace, CVR_H_WATCH, CVR_H_ALERT):
            span = slice(run.start, run.end + 1)
            n, o = sum(clicks[span]), sum(orders[span])
            observed = o / n if n else 0.0
            out.append(_episode(c, Metric.CVR, direction, run, mon_days, p0, observed))
    return out


def _gaussian(
    c: CampaignInfo,
    series: dict[date, MetricTotals],
    mon_days: list[date],
    ref_days: list[date],
    metric: Metric,
    skipped: list[Skipped],
) -> list[Episode]:
    def raw(d: date) -> float | None:
        t = series.get(d)
        if t is None:
            return None
        if metric is Metric.CPC:
            return (
                math.log(t.spend_paise / t.clicks)
                if t.clicks >= ANOMALY_MIN_CLICKS_FOR_CPC_DAY and t.spend_paise > 0
                else None
            )
        return math.log(t.spend_paise) if t.spend_paise > 0 else None

    # Spend has a weekday rhythm: remove each weekday's average offset (estimated on reference).
    offset = {wd: 0.0 for wd in range(7)}
    if metric is Metric.SPEND:
        ref_vals = [(d.weekday(), v) for d in ref_days if (v := raw(d)) is not None]
        if ref_vals:
            overall = sum(v for _, v in ref_vals) / len(ref_vals)
            for wd in range(7):
                vals = [v for w, v in ref_vals if w == wd]
                if len(vals) >= 3:
                    offset[wd] = sum(vals) / len(vals) - overall

    def adjusted(d: date) -> float | None:
        v = raw(d)
        return None if v is None else v - offset[d.weekday()]

    ref_values = [v for d in ref_days if (v := adjusted(d)) is not None]
    if len(ref_values) < ANOMALY_MIN_REFERENCE_DAYS:
        skipped.append(Skipped(c.external_id, metric, "not enough reference days"))
        return []
    try:
        centre, sigma = robust_center_scale(ref_values)
    except ValueError:
        skipped.append(Skipped(c.external_id, metric, "reference has no spread"))
        return []
    z = [None if (v := adjusted(d)) is None else (v - centre) / sigma for d in mon_days]

    ref_total = _total(series, ref_days)
    if metric is Metric.CPC:
        ref_value = ref_total.spend_paise / ref_total.clicks if ref_total.clicks else 0.0
    else:
        spend_days = [
            series[d].spend_paise for d in ref_days if d in series and series[d].spend_paise > 0
        ]
        ref_value = sum(spend_days) / len(spend_days) if spend_days else 0.0

    out: list[Episode] = []
    for direction in (Direction.UP, Direction.DOWN):
        trace: Trace = gaussian_trace(z, direction, GAUSS_K)
        for run in find_runs(trace, GAUSS_H_WATCH, GAUSS_H_ALERT):
            days = mon_days[run.start : run.end + 1]
            window = _total(series, days)
            if metric is Metric.CPC:
                observed = window.spend_paise / window.clicks if window.clicks else 0.0
            else:
                vals = [
                    series[d].spend_paise for d in days if d in series and series[d].spend_paise > 0
                ]
                observed = sum(vals) / len(vals) if vals else 0.0
            out.append(_episode(c, metric, direction, run, mon_days, ref_value, observed))
    return out
