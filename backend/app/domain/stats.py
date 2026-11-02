"""Small statistics helpers for judging noisy conversion data. Pure; ``math`` only.

These are inference helpers, not money: they use floats (the exact-``Decimal`` policy applies to
KPIs). All probabilities are in [0, 1].
"""

import math


def _log_sum_pmf(lo: int, hi: int, n: int, p: float) -> float:
    """log of sum_{i=lo..hi} Binomial(n, p).pmf(i), for 0 < p < 1."""
    log_p, log_q = math.log(p), math.log1p(-p)
    base = math.lgamma(n + 1)
    terms = [
        base - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * log_p + (n - i) * log_q
        for i in range(lo, hi + 1)
    ]
    peak = max(terms)
    return peak + math.log(sum(math.exp(t - peak) for t in terms))


def binomial_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p), computed in log space so large n does not underflow."""
    if n < 0 or k < 0 or not 0.0 <= p <= 1.0:
        raise ValueError("require n >= 0, k >= 0 and 0 <= p <= 1")
    if k >= n or p == 0.0:
        return 1.0
    if p == 1.0:
        return 0.0  # k < n and every trial succeeds
    return min(1.0, math.exp(_log_sum_pmf(0, k, n, p)))


def binomial_upper_tail(k: int, n: int, p: float) -> float:
    """P(X >= k). Sums whichever tail has fewer terms, so cost is O(min(k, n - k))."""
    if n < 0 or k < 0 or not 0.0 <= p <= 1.0:
        raise ValueError("require n >= 0, k >= 0 and 0 <= p <= 1")
    if k == 0 or p == 1.0:
        return 1.0
    if k > n or p == 0.0:
        return 0.0
    if k <= n - k + 1:
        return max(0.0, 1.0 - binomial_cdf(k - 1, n, p))
    return min(1.0, math.exp(_log_sum_pmf(k, n, n, p)))


def chance_of_at_most(orders: int, clicks: int, reference_cvr: float) -> float:
    """How likely is it to see ``orders`` or fewer from ``clicks`` if the true conversion rate
    were ``reference_cvr``? Small values mean the shortfall is unlikely to be luck."""
    return binomial_cdf(orders, clicks, reference_cvr)


def exact_lower_bound(successes: int, trials: int, alpha: float = 0.05) -> float:
    """Exact (Clopper-Pearson) one-sided lower confidence bound of a proportion.

    The smallest rate that still makes ``successes`` or more out of ``trials`` plausible at level
    ``alpha``. Deliberately conservative for tiny samples: 1 order from 1 click scores 0.05, far
    below 20 orders from 100 clicks (about 0.14). (The Wilson bound, which we tried first,
    ranks 1/1 *above* 20/100 and would let lucky small samples top an opportunity list.)
    """
    if trials <= 0 or successes <= 0:
        return 0.0
    if not 0 <= successes <= trials:
        raise ValueError("successes must be within 0..trials")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    if successes == trials:
        return float(alpha ** (1.0 / trials))
    lo, hi = 0.0, successes / trials
    for _ in range(45):  # bisection: P(X >= successes | p) is increasing in p
        mid = (lo + hi) / 2
        if binomial_upper_tail(successes, trials, mid) < alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def clicks_needed_to_rule_out(reference_cvr: float, max_chance: float) -> int | None:
    """Fewest clicks with zero orders so that zero orders would happen by chance with probability
    at most ``max_chance`` at ``reference_cvr``. ``None`` if the rate is 0 (cannot be ruled out)."""
    if not 0.0 < max_chance < 1.0:
        raise ValueError("max_chance must be in (0, 1)")
    if reference_cvr <= 0.0:
        return None
    if reference_cvr >= 1.0:
        return 1
    return math.ceil(math.log(max_chance) / math.log1p(-reference_cvr))
