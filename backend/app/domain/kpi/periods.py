"""Period aggregation and previous-period comparison, with attribution-maturity handling.

The most recent days under-report orders and sales (attribution lag), which inflates ACOS. By
default, comparisons therefore use only mature data: the current period is truncated at the last
mature day and the previous period is the equal-length window immediately before it, so both
sides are aligned.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from app.domain.ads.models import DailyMetrics
from app.domain.kpi.metrics import KpiChange, Kpis, MetricTotals, compare_totals


class NoMatureDataError(ValueError):
    """The requested period contains no mature days."""


@dataclass(frozen=True)
class DateRange:
    """Inclusive date range."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"end {self.end} is before start {self.start}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def previous(self) -> "DateRange":
        """Equal-length range ending the day before this one starts."""
        return DateRange(self.start - timedelta(days=self.days), self.start - timedelta(days=1))

    def truncate_end(self, last_day: date) -> "DateRange":
        """This range cut off after ``last_day``; raises if nothing remains."""
        if last_day < self.start:
            raise NoMatureDataError(f"no mature data in {self.start}..{self.end}")
        return DateRange(self.start, min(self.end, last_day))


def totals_of(row: DailyMetrics) -> MetricTotals:
    return MetricTotals(
        row.impressions, row.clicks, row.spend_paise, row.sales_paise, row.orders, row.units
    )


def _single_level(rows: Iterable[DailyMetrics]) -> list[DailyMetrics]:
    """Aggregating several hierarchy levels together would double count. Refuse."""
    materialized = list(rows)
    if len({r.entity_type for r in materialized}) > 1:
        raise ValueError("rows mix entity types; aggregate one level at a time")
    return materialized


def infer_mature_through(rows: Iterable[DailyMetrics]) -> date | None:
    """Last mature day, or ``None`` when every row is mature."""
    immature = [r.date for r in rows if not r.is_mature]
    return min(immature) - timedelta(days=1) if immature else None


def daily_totals(rows: Iterable[DailyMetrics]) -> dict[date, MetricTotals]:
    """Per-day totals over one hierarchy level, in date order."""
    by_day: dict[date, MetricTotals] = defaultdict(MetricTotals)
    for r in _single_level(rows):
        by_day[r.date] = by_day[r.date] + totals_of(r)
    return dict(sorted(by_day.items()))


@dataclass(frozen=True)
class PeriodAggregate:
    range: DateRange
    totals: MetricTotals
    active_days: int  # distinct days that contributed data
    immature_days_included: int  # days still inside the attribution window

    @property
    def kpis(self) -> Kpis:
        return Kpis.from_totals(self.totals)


def aggregate_period(
    rows: Iterable[DailyMetrics], period: DateRange, *, include_immature: bool = True
) -> PeriodAggregate:
    totals = MetricTotals()
    active: set[date] = set()
    immature: set[date] = set()
    for r in _single_level(rows):
        if not period.contains(r.date) or (not include_immature and not r.is_mature):
            continue
        totals = totals + totals_of(r)
        active.add(r.date)
        if not r.is_mature:
            immature.add(r.date)
    return PeriodAggregate(period, totals, len(active), len(immature))


@dataclass(frozen=True)
class PeriodComparison:
    current: PeriodAggregate
    previous: PeriodAggregate
    changes: dict[str, KpiChange]
    truncated_days: int  # days cut from the requested period to keep only mature data


def compare_periods(
    rows: Sequence[DailyMetrics],
    current: DateRange,
    *,
    mature_through: date | None = None,
    include_immature: bool = False,
) -> PeriodComparison:
    """Compare ``current`` with the equal-length period immediately before it.

    With ``include_immature=False`` (default) the current period is truncated at the last mature
    day (``mature_through``, inferred from the rows when omitted) and the previous period is
    aligned to the truncated window. Use whole-week windows to keep weekday mix comparable.
    """
    effective = current
    if not include_immature:
        last_mature = mature_through if mature_through is not None else infer_mature_through(rows)
        if last_mature is not None:
            effective = current.truncate_end(last_mature)
    cur = aggregate_period(rows, effective, include_immature=include_immature)
    prev = aggregate_period(rows, effective.previous(), include_immature=include_immature)
    return PeriodComparison(
        current=cur,
        previous=prev,
        changes=compare_totals(cur.totals, prev.totals),
        truncated_days=current.days - effective.days,
    )
