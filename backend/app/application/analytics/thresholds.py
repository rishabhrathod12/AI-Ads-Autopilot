"""Analytics thresholds, in one place so they are documented, reviewable and testable.

These are business rules, not statistics learned from data. Each has a rationale; change them
deliberately (and update docs/ANALYTICS.md).
"""

from decimal import Decimal

# Default target ACOS (owner decision, M3 plan). Passed per request; persisted per account in M6.
DEFAULT_TARGET_ACOS_PCT = Decimal(30)

# A campaign needs this many clicks in the period before its efficiency is judged at all.
# With a typical 5-10% conversion rate, 100 clicks is roughly 5-10 orders: thin but usable.
MIN_CLICKS_FOR_HEALTH = 100

# Up to this multiple of the target ACOS is "watch" (close to target); beyond it is "unhealthy".
WATCH_ACOS_MULTIPLE = Decimal("1.3")

# A day "hits the cap" when spend reaches this share of the daily budget (Amazon can pace a
# little under budget), and a campaign is budget constrained when this share of days do.
BUDGET_CAP_SPEND_SHARE = Decimal("0.9")
BUDGET_CONSTRAINED_DAYS_SHARE = Decimal("0.5")

# ---- keyword / search-term findings ---------------------------------------------------------
# Below this many clicks an item is "low data": never flagged, only counted.
MIN_CLICKS_FOR_FINDING = 10

# A zero-order target/term is *confirmed* waste when, at its campaign's conversion rate, zero
# orders would happen by chance at most 5% of the time; *watch* at 20%. (About 32 clicks at a 9%
# CVR for confirmed.) The reference rate must itself rest on at least MIN_CLICKS_FOR_HEALTH clicks.
WASTE_CONFIRMED_MAX_CHANCE = 0.05
WASTE_WATCH_MAX_CHANCE = 0.20

# Underperforming: has orders, but so few for its clicks that luck is an unlikely explanation
# (lower-tail chance <= 5%) AND its ACOS is above target.
UNDERPERFORMING_MAX_CHANCE = 0.05

# Opportunity: a converting term not yet targeted exactly. Needs real orders, ACOS within target,
# and is ranked by the exact 95% lower bound of its conversion rate.
OPPORTUNITY_MIN_ORDERS = 2
OPPORTUNITY_BOUND_ALPHA = 0.05

# ---- anomaly detection ------------------------------------------------------------------------
# Watch the most recent ANOMALY_MONITOR_DAYS mature days against the ANOMALY_REFERENCE_DAYS before.
ANOMALY_MONITOR_DAYS = 14
ANOMALY_REFERENCE_DAYS = 56
# Reference needs this much evidence before a campaign can be monitored at all.
ANOMALY_MIN_REFERENCE_CLICKS = 500
ANOMALY_MIN_REFERENCE_DAYS = 28  # for the CPC / spend detectors
ANOMALY_MIN_CLICKS_FOR_CPC_DAY = 5

# Conversion rate (Bernoulli CUSUM): the design shifts are "rate fell to 60%" / "rose to 150%".
CVR_RATIO_DOWN = 0.6
CVR_RATIO_UP = 1.5
# Calibrated by simulating NO change (binomial noise, rate estimated from 56 reference days,
# 20-120 clicks/day): false alarm <= 3% / <= 0.5% per campaign and direction per 14-day window.
CVR_H_WATCH = 4.7
CVR_H_ALERT = 6.7

# CPC and spend (Gaussian CUSUM on standardised logs, slack k=0.5 sigma). Calibrated the same way
# with a MAD scale from 56 days and heavy-tailed (t5) noise, the conservative case.
GAUSS_K = 0.5
GAUSS_H_WATCH = 7.0
GAUSS_H_ALERT = 10.5

# Default dashboard window.
DEFAULT_WINDOW_DAYS = 28
