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

# Default dashboard window.
DEFAULT_WINDOW_DAYS = 28
