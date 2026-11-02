"""CUSUM change detection. Pure; no I/O.

Why CUSUM: ad metrics shift gradually and traffic per campaign is modest, so a single-day z-score
either misses real shifts or fires constantly. CUSUM accumulates evidence day by day, so sustained
changes cross the threshold even when no single day looks odd, and one-day noise decays away.

* Conversion rate uses a Bernoulli log-likelihood CUSUM on (orders, clicks): it respects counts, so
  a day with 3 clicks counts far less than a day with 300.
* CPC and spend use a Gaussian CUSUM on standardised log values with a robust (MAD) scale.

Thresholds are calibrated by simulating a process with *no change* (see thresholds.py); they are
not tuned to any planted scenario.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import median


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"


@dataclass(frozen=True)
class Trace:
    """Day-by-day CUSUM statistic and the evidence each day added (negative = counter-evidence)."""

    path: list[float]
    increments: list[float]


@dataclass(frozen=True)
class Run:
    """A stretch where evidence was accumulating (statistic > 0) and it crossed the watch bar."""

    start: int  # first day index with statistic > 0 (the estimated onset)
    end: int  # last day index of the stretch
    peak: float
    first_watch: int
    first_alert: int | None
    ongoing: bool  # still adding evidence over the last days of the window


def bernoulli_trace(
    orders: Sequence[int], clicks: Sequence[int], p0: float, direction: Direction, ratio: float
) -> Trace:
    """Evidence that the rate moved from ``p0`` to ``p0 * ratio`` (ratio < 1 for DOWN, > 1 for UP).

    Each click contributes log(p1/p0) if it ordered and log((1-p1)/(1-p0)) if not; days with no
    clicks add nothing.
    """
    if not 0.0 < p0 < 1.0:
        raise ValueError("p0 must be strictly between 0 and 1")
    if (direction is Direction.DOWN and not 0.0 < ratio < 1.0) or (
        direction is Direction.UP and ratio <= 1.0
    ):
        raise ValueError("ratio must be < 1 for DOWN and > 1 for UP")
    p1 = min(p0 * ratio, 0.999)
    on_order, on_miss = math.log(p1 / p0), math.log((1 - p1) / (1 - p0))
    path, increments, s = [], [], 0.0
    for o, c in zip(orders, clicks, strict=True):
        if o < 0 or c < 0 or o > c:
            raise ValueError("require 0 <= orders <= clicks")
        inc = o * on_order + (c - o) * on_miss
        s = max(0.0, s + inc)
        path.append(s)
        increments.append(inc)
    return Trace(path, increments)


def gaussian_trace(z: Sequence[float | None], direction: Direction, k: float = 0.5) -> Trace:
    """One-sided CUSUM on standardised values. ``None`` (no information) leaves it unchanged."""
    sign = 1.0 if direction is Direction.UP else -1.0
    path, increments, s = [], [], 0.0
    for value in z:
        inc = 0.0 if value is None else sign * value - k
        s = s if value is None else max(0.0, s + inc)
        path.append(s)
        increments.append(inc)
    return Trace(path, increments)


def find_runs(trace: Trace, h_watch: float, h_alert: float, *, recent_days: int = 3) -> list[Run]:
    """Runs of positive statistic whose peak reaches ``h_watch``."""
    if h_alert < h_watch:
        raise ValueError("h_alert must be >= h_watch")
    runs: list[Run] = []
    n, i = len(trace.path), 0
    while i < n:
        if trace.path[i] <= 0.0:
            i += 1
            continue
        j = i
        while j + 1 < n and trace.path[j + 1] > 0.0:
            j += 1
        segment = trace.path[i : j + 1]
        peak = max(segment)
        if peak >= h_watch:
            watch = next(i + d for d, v in enumerate(segment) if v >= h_watch)
            alert = next((i + d for d, v in enumerate(segment) if v >= h_alert), None)
            tail = trace.increments[max(i, n - recent_days) : n] if j == n - 1 else []
            runs.append(Run(i, j, peak, watch, alert, j == n - 1 and sum(tail) > 0.0))
        i = j + 1
    return runs


def robust_center_scale(values: Sequence[float]) -> tuple[float, float]:
    """Median and MAD-based sigma. Raises if the values carry no spread to standardise by."""
    if len(values) < 2:
        raise ValueError("need at least two values")
    centre = median(values)
    sigma = 1.4826 * median(abs(v - centre) for v in values)
    if sigma <= 0.0:
        raise ValueError("values have no spread")
    return centre, sigma
