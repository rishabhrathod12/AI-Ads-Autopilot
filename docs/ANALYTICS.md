# Analytics Reference (M3)

What the analytics layer computes, the rules it applies, and why. Every threshold lives in
`backend/app/application/analytics/thresholds.py` with its rationale; this document explains them.

> All data to date is **MOCK** (synthetic). Results validate the implementation, not real-account accuracy.

## 1. KPIs (`app/domain/kpi`)

| KPI | Formula | Unit |
|---|---|---|
| ACOS | spend / sales x 100 | % |
| ROAS | sales / spend | x |
| CTR | clicks / impressions x 100 | % |
| CPC | spend / clicks | paise |
| CVR | orders / clicks x 100 | % |
| AOV | sales / orders | paise |

`sales` is **advertising-attributed** sales, never total business revenue. Policies ([ADR 0011](ADR/0011-kpi-aggregation-and-undefined-ratios.md)):

- **Aggregate, then take the ratio.** Counts are summed over rows/entities/days first. Averaging per-day or per-campaign ratios is wrong (a 10% and a 100% ACOS campaign with spend 100 and 500 average to 55%, but the true ACOS is 40%).
- **A zero denominator is `None`/`null`, never 0 or infinity.** Spend with no sales has an undefined ACOS, not a perfect one.
- Exact `Decimal` (28 digits); rounding only at presentation. Money is integer paise.

## 2. Attribution maturity

The most recent days under-report orders and sales (attribution lag), which inflates ACOS. Every row carries `is_mature`; the mock models a 7-day window with the last 3 days incomplete.

- Dashboards **display** all days, flagging immature ones.
- Anything that **judges** (comparisons, health, findings, anomalies, forecasts) uses mature days only.
- Period comparisons truncate the current period at the last mature day and realign the previous period to the same length, so the lag is never read as a real ACOS rise.

## 3. Dashboard and campaigns

- `dashboard_summary`: totals, daily trend, mature-aligned comparison with the previous equal-length period.
- **ACOS change explanation** (`explain_acos_change`): ACOS = CPC / (CVR x AOV) is an exact identity, so `ln(ACOS1/ACOS0)` splits exactly into CPC, CVR and AOV effects, converted to percentage points of the ACOS change. This is bookkeeping, not causal inference: it says which factor moved, not why. It is the deterministic tool behind "Why did my ACOS increase?".
- **Campaign health** (target ACOS default 30%, a request parameter):

| Status | Rule |
|---|---|
| insufficient_data | fewer than 100 clicks, or orders without attributed sales |
| unhealthy | zero orders (100+ clicks), or ACOS > 1.3 x target |
| watch | target < ACOS <= 1.3 x target |
| healthy | ACOS <= target |

- **Budget:** a day "hits the cap" at >= 90% of daily budget; a campaign is `constrained` when >= 50% of days do. Uses today's budget for the whole period (budget history is not available).
- **Spend distribution:** share and cumulative share per campaign.

## 4. Keyword and search-term findings (`keywords.py`)

Findings with evidence, **not recommendations**: M6 turns them into proposals that need human approval. Mature days only; items under 10 clicks are counted as `low_data`, never flagged ([ADR 0012](ADR/0012-statistical-evidence-over-fixed-thresholds.md)).

| Finding | Rule |
|---|---|
| Wasted target | zero orders; at the campaign's own CVR, zero orders would happen by luck <= 5% of the time (**confirmed**) or <= 20% (**watch**). About 32 clicks at a 9% CVR for confirmed |
| Underperforming target | has orders, but so few for its clicks that luck is <= 5% likely, and ACOS > target |
| Negative-keyword candidate | a zero-order search term judged like waste, excluding terms that are themselves keywords, terms whose keyword is already flagged (act on the keyword once), and terms already negated |
| Opportunity | converting term from an auto/broad/phrase target, >= 2 orders, ACOS <= target, not already an exact keyword; ranked by the exact 95% lower bound of its conversion rate. `strong` if that bound is at least the campaign's average CVR |

The reference conversion rate is the campaign's (needs 100+ clicks), else the account's. Monthly spend at risk is an estimate that assumes spend continues at the same rate.

## 5. Anomaly detection (`anomalies.py`, `domain/anomaly.py`)

CUSUM change detection over the last 14 mature days against the 56 before them, per campaign ([ADR 0012](ADR/0012-statistical-evidence-over-fixed-thresholds.md)).

- **Conversion rate:** Bernoulli log-likelihood CUSUM on (orders, clicks); designed to catch "fell to 60%" / "rose to 150%". Days with few clicks count for little.
- **CPC and spend:** Gaussian CUSUM on standardised logs with a robust (MAD) scale; spend is weekday-adjusted.
- **Thresholds** were calibrated by simulating *no change* (false alarm <= 3% for `watch`, <= 0.5% for `alert` per campaign/direction per window), never from planted scenarios.
- Each episode has onset, detection day, severity, status (`ongoing` / `subsided` - a promotion that faded), observed vs reference value and evidence strength; campaigns with a shift also get the ACOS driver breakdown.
- **Honest limit:** at ~40-60 clicks/day a campaign's halved conversion rate takes about a week to confirm at `watch` level. Faster detection would mean many false alarms. Campaigns without enough history are listed as `skipped`, never guessed.

## 6. Forecasting (`app/application/forecasting`)

Daily spend and sales, 14-day horizon, account and campaign level; ACOS is derived from the two forecasts, never forecast alone ([ADR 0013](ADR/0013-forecast-model-selection-rule.md)).

- Needs >= 74 mature days (60 to train + one 14-day fold); otherwise it refuses.
- Candidates: naive, 7-day moving average, seasonal-naive, 4-week seasonal mean, Holt-Winters (damped trend), ridge, and shallow gradient boosting on lag features.
- **Selection:** rolling-origin backtest (expanding window, weekly steps). A learned model is chosen only if its pooled MAE beats seasonal-naive by >= 5% **and** it wins more than half the folds; otherwise the best baseline is used and the reason is stored.
- **Intervals** (80%) come from the chosen model's own out-of-sample relative errors; their coverage is checked leave-one-fold-out.
- A failed model is reported as failed and excluded, never silently replaced.
- Forecasts are stored in `forecast_runs` (immutable, tenant-isolated) with the evidence for the model choice.
- Cost: about 3 s per series (6 s for the account). Campaign-level forecasts take over a minute for 12 campaigns, so they are a background-job concern, not a request path.

## 7. API

See [API_DESIGN.md](API_DESIGN.md#implemented-m3-analytics-endpoints). Money is integer paise, undefined ratios are `null`, and every response carries `meta` (data source, `as_of`, `mature_through`, MOCK notice).

## 8. Known limitations

- Synthetic data: distributions are plausible, not calibrated to real Amazon.in accounts.
- Several accuracy checks failed or were marginal; see [ML_EVALUATION.md](ML_EVALUATION.md).
- Query-performance targets (dashboard under 1 s on the 646k-row seed) and index review are **not yet verified**.
- Tenant identity is a development-only header; real authentication is required before any non-local deployment.
