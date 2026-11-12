"""Daily spend/sales series from a generator that is NOT the mock-data generator.

Built to differ structurally from ``app.infrastructure.providers.mock.generator`` (a fixed weekly
profile plus smooth noise): here the level drifts and takes step changes, the weekly shape's
strength varies and evolves, promotions (possibly inside the holdout) and outliers occur, and sales
are spend times a wandering return-on-ad-spend rather than a sum of latent per-term orders. It
shares no code, constants or random streams with the mock generator. It is still synthetic: it
shows how methods behave under harder, differently-shaped conditions, not how they behave on real
accounts.
"""

import numpy as np
from numpy.typing import NDArray

FloatArr = NDArray[np.float64]


def make_series(seed: int, days: int = 131) -> tuple[FloatArr, FloatArr]:
    """(spend, sales) in paise, ``days`` consecutive daily values, deterministic in ``seed``."""
    rng = np.random.default_rng(1_000_003 * seed + 17)
    t = np.arange(days)
    # level: log random walk with drift and occasional step changes
    drift = rng.normal(0, 0.002)
    log_level = np.log(rng.uniform(4e4, 4e6)) + np.cumsum(rng.normal(drift, 0.012, days))
    steps = rng.random(days) < 1 / 55
    log_level += np.cumsum(steps * rng.normal(0, 0.18, days))
    # weekly shape: amplitude differs by seed and drifts slowly
    base_shape = rng.normal(0, 1, 7)
    base_shape -= base_shape.mean()
    amp = rng.uniform(0.02, 0.22) * (1 + 0.5 * np.sin(t / rng.uniform(20, 60)))
    weekly = amp * base_shape[t % 7]
    # promotions: 0-2 events of 1-3 days, uplift 1.4-2.6x, anywhere (holdout included)
    promo = np.zeros(days)
    for _ in range(int(rng.integers(0, 3))):
        start = int(rng.integers(0, days - 3))
        promo[start : start + int(rng.integers(1, 4))] = np.log(rng.uniform(1.4, 2.6))
    # noise: lognormal with seed-specific scale, plus rare outliers
    noise = rng.normal(0, rng.uniform(0.06, 0.28), days)
    outlier = rng.random(days) < 0.02
    noise += outlier * rng.choice([np.log(0.3), np.log(2.2)], days)
    spend = np.exp(log_level + weekly + promo + noise)
    # return on ad spend: AR(1) around a seed-specific mean
    roas_log = np.empty(days)
    roas_log[0] = np.log(rng.uniform(2.0, 6.0))
    mu = roas_log[0]
    for i in range(1, days):
        roas_log[i] = mu + 0.8 * (roas_log[i - 1] - mu) + rng.normal(0, 0.08)
    sales = spend * np.exp(roas_log) * np.exp(rng.normal(0, 0.1, days))
    return np.round(spend), np.round(sales)
