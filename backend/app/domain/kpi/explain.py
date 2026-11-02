"""Arithmetic decomposition of an ACOS change. Pure; no I/O.

ACOS is an exact identity of three drivers (CVR as a fraction):

    ACOS = spend / sales = CPC / (CVR x AOV)

so   ln(ACOS1 / ACOS0) = ln(CPC1/CPC0) - ln(CVR1/CVR0) - ln(AOV1/AOV0).

Each driver's log effect is converted to percentage points of the ACOS change in proportion to its
share of the total. This is bookkeeping, not causal inference: it says *which factor moved*, not
*why* it moved (bids, seasonality, listing changes, competitors...).
"""

from dataclasses import dataclass
from decimal import Context, Decimal
from typing import Literal

from app.domain.kpi.metrics import MetricTotals, acos_pct, aov_paise, cpc_paise, cvr_pct

_CTX = Context(prec=28)
NOTE = (
    "Arithmetic attribution of the ACOS change to CPC, conversion rate and order value. "
    "It shows which factor moved, not why."
)


@dataclass(frozen=True)
class Driver:
    name: Literal["cpc", "cvr", "aov"]
    current: Decimal
    previous: Decimal
    log_effect: Decimal  # positive = pushed ACOS up
    contribution_points: Decimal  # share of the ACOS change, in percentage points


@dataclass(frozen=True)
class AcosExplanation:
    previous_pct: Decimal
    current_pct: Decimal
    change_points: Decimal
    drivers: tuple[Driver, ...]  # largest absolute contribution first
    note: str = NOTE

    @property
    def main_driver(self) -> Driver:
        return self.drivers[0]


def explain_acos_change(current: MetricTotals, previous: MetricTotals) -> AcosExplanation | None:
    """``None`` when any factor is undefined or zero in either period (no clicks, orders, ...)."""
    values = {
        "cpc": (
            cpc_paise(current.spend_paise, current.clicks),
            cpc_paise(previous.spend_paise, previous.clicks),
        ),
        "cvr": (cvr_pct(current.orders, current.clicks), cvr_pct(previous.orders, previous.clicks)),
        "aov": (
            aov_paise(current.sales_paise, current.orders),
            aov_paise(previous.sales_paise, previous.orders),
        ),
    }
    cur_acos = acos_pct(current.spend_paise, current.sales_paise)
    prev_acos = acos_pct(previous.spend_paise, previous.sales_paise)
    if cur_acos is None or prev_acos is None or cur_acos == 0 or prev_acos == 0:
        return None
    effects: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for name, (cur, prev) in values.items():
        if cur is None or prev is None or cur == 0 or prev == 0:
            return None
        log_ratio = _CTX.divide(cur, prev).ln(_CTX)
        sign = 1 if name == "cpc" else -1  # higher CVR / AOV lowers ACOS
        effects[name] = (cur, prev, log_ratio * sign)

    total = sum((e[2] for e in effects.values()), Decimal(0))
    change = cur_acos - prev_acos
    drivers = [
        Driver(
            name=name,  # type: ignore[arg-type]
            current=cur,
            previous=prev,
            log_effect=effect,
            contribution_points=Decimal(0) if total == 0 else _CTX.divide(change * effect, total),
        )
        for name, (cur, prev, effect) in effects.items()
    ]
    drivers.sort(key=lambda d: abs(d.contribution_points), reverse=True)
    return AcosExplanation(prev_acos, cur_acos, change, tuple(drivers))
