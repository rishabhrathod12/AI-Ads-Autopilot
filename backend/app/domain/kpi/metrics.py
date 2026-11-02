"""Advertising KPI formulas. Pure, decimal-safe, no I/O.

Policies (see docs/ANALYTICS.md):
  * Aggregate first, then take the ratio. Never average ratios across rows or entities.
  * A zero denominator yields ``None`` ("undefined"), never 0 or infinity. An ACOS of 0 would
    wrongly read as a perfect campaign when there were simply no sales.
  * Money is integer paise. Ratios are exact ``Decimal`` (28 significant digits); round only for
    presentation with ``round_dp``.
  * ``sales_paise`` is advertising-attributed sales, NOT total business revenue.

    ACOS = spend / sales x 100        ROAS = sales / spend
    CTR  = clicks / impressions x 100 CPC  = spend / clicks   (paise)
    CVR  = orders / clicks x 100      AOV  = sales / orders   (paise)
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal

_CTX = Context(prec=28, rounding=ROUND_HALF_EVEN)
KPI_NAMES = ("acos_pct", "roas", "ctr_pct", "cpc_paise", "cvr_pct", "aov_paise")
TOTAL_NAMES = ("impressions", "clicks", "spend_paise", "sales_paise", "orders", "units")


def _ratio(numerator: int, denominator: int, multiplier: int = 1) -> Decimal | None:
    """numerator x multiplier / denominator, exactly; ``None`` when the denominator is zero."""
    if denominator == 0:
        return None
    return _CTX.divide(Decimal(numerator * multiplier), Decimal(denominator))


def acos_pct(spend_paise: int, sales_paise: int) -> Decimal | None:
    return _ratio(spend_paise, sales_paise, 100)


def roas(sales_paise: int, spend_paise: int) -> Decimal | None:
    return _ratio(sales_paise, spend_paise)


def ctr_pct(clicks: int, impressions: int) -> Decimal | None:
    return _ratio(clicks, impressions, 100)


def cpc_paise(spend_paise: int, clicks: int) -> Decimal | None:
    return _ratio(spend_paise, clicks)


def cvr_pct(orders: int, clicks: int) -> Decimal | None:
    return _ratio(orders, clicks, 100)


def aov_paise(sales_paise: int, orders: int) -> Decimal | None:
    return _ratio(sales_paise, orders)


def round_dp(value: Decimal | None, places: int = 2) -> Decimal | None:
    """Presentation rounding (half up). ``None`` stays ``None``."""
    if value is None:
        return None
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class MetricTotals:
    """Additive counts for any grouping. All KPIs derive from these, never from other KPIs."""

    impressions: int = 0
    clicks: int = 0
    spend_paise: int = 0
    sales_paise: int = 0
    orders: int = 0
    units: int = 0

    def __post_init__(self) -> None:
        for name in TOTAL_NAMES:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer, got {value!r}")

    def __add__(self, other: "MetricTotals") -> "MetricTotals":
        return MetricTotals(
            self.impressions + other.impressions,
            self.clicks + other.clicks,
            self.spend_paise + other.spend_paise,
            self.sales_paise + other.sales_paise,
            self.orders + other.orders,
            self.units + other.units,
        )

    @classmethod
    def sum(cls, items: Iterable["MetricTotals"]) -> "MetricTotals":
        total = cls()
        for item in items:
            total = total + item
        return total


@dataclass(frozen=True)
class Kpis:
    acos_pct: Decimal | None
    roas: Decimal | None
    ctr_pct: Decimal | None
    cpc_paise: Decimal | None
    cvr_pct: Decimal | None
    aov_paise: Decimal | None

    @classmethod
    def from_totals(cls, t: MetricTotals) -> "Kpis":
        return cls(
            acos_pct=acos_pct(t.spend_paise, t.sales_paise),
            roas=roas(t.sales_paise, t.spend_paise),
            ctr_pct=ctr_pct(t.clicks, t.impressions),
            cpc_paise=cpc_paise(t.spend_paise, t.clicks),
            cvr_pct=cvr_pct(t.orders, t.clicks),
            aov_paise=aov_paise(t.sales_paise, t.orders),
        )


@dataclass(frozen=True)
class KpiChange:
    """Change of one metric between two periods. Undefined inputs give undefined results."""

    current: Decimal | None
    previous: Decimal | None

    @property
    def absolute(self) -> Decimal | None:
        """current - previous. For percentage KPIs this is a change in percentage points."""
        if self.current is None or self.previous is None:
            return None
        return self.current - self.previous

    @property
    def relative_pct(self) -> Decimal | None:
        """(current - previous) / previous x 100; undefined when previous is missing or zero."""
        if self.current is None or self.previous is None or self.previous == 0:
            return None
        return _CTX.divide((self.current - self.previous) * 100, self.previous)


def compare_totals(current: MetricTotals, previous: MetricTotals) -> dict[str, KpiChange]:
    """Per-metric changes for every KPI and every additive total, keyed by metric name."""
    cur_k, prev_k = Kpis.from_totals(current), Kpis.from_totals(previous)
    changes = {n: KpiChange(getattr(cur_k, n), getattr(prev_k, n)) for n in KPI_NAMES}
    for n in TOTAL_NAMES:
        changes[n] = KpiChange(Decimal(getattr(current, n)), Decimal(getattr(previous, n)))
    return changes
