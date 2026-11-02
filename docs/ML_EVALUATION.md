# ML and Statistical Evaluation (M3)

How the analytics were scored, what the results were, and where they fell short. Failures are
reported as they happened; no threshold was adjusted after a scored run.

> **Read this first.** The scenarios being detected (wasted keywords, anomalies, weekly patterns) were
> planted by the same synthetic-data generator the detectors are tested on. These scores show the
> implementation behaves as designed against that generator. **They do not measure accuracy on real
> Amazon accounts**, whose seasonality, promotions and noise are far less regular.

## 1. Method

- **Pre-registration.** Pass/fail criteria were written into the evaluation file *before* the
  detector/model existed (or before its first scored run) and are quoted in each file's docstring.
- **Held-out seeds.** Each evaluation uses seeds never used to build the rules: 1-5 (keywords),
  11-15 (anomalies), 21-25 (forecasts). The default dataset (seed 42) was inspected while building and is never scored.
- **Calibration without the answer.** Anomaly thresholds were set by simulating a process with *no
  change*, not from the planted scenarios.
- **Nested forecast evaluation.** The last 14 mature days of each series are hidden; the forecaster sees
  only earlier days, runs its own backtest to pick a model, then forecasts the hidden days.
- **Policy on failure.** A failed criterion is recorded as a strict `xfail` with its diagnosis. Changing a
  rule or criterion afterwards requires fresh seeds and an explicit owner decision.

Reproduce: `cd backend && pytest -m eval tests/evals -s` (slow: ~7 minutes for the forecast evaluation).

## 2. Step 4: keyword and search-term findings (seeds 1-5)

| Check (mean over 5 seeds) | Threshold | Result |
|---|---|---|
| Wasted targets: precision of "confirmed" | >= 0.85 | **0.67 - fail** |
| Wasted targets: recall (confirmed + watch) | >= 0.75 | 0.96 |
| Negative terms: precision of "confirmed" | >= 0.85 | 0.86 |
| Negative terms: recall (confirmed + watch) | >= 0.70 | **0.64 - fail** |
| Opportunities: planted-winner recall | >= 0.80 | 0.83 |
| Opportunities: precision of "strong" items | >= 0.80 | 0.96 |

**Diagnosis (post hoc; not a pass):**

- *Wasted precision.* All 54 "false positives" convert **below their campaign's average**; 70% below half of it
  (median 0.43x). They are genuinely poor converters that happened to book zero orders. The pre-registered truth
  counted only targets with an exactly-zero conversion rate, which is too narrow a definition of waste.
- *Negative recall.* Of 57 missed planted terms, 37 had too few clicks to tell zero orders from bad luck, 13 were
  excluded by design (the parent keyword is already flagged; the fix belongs on the keyword), 7 were other.

**Open decision:** keep the rules and re-state the criteria (truth = "true CVR below half the campaign's"; drop the
undetectable and excluded terms from the recall denominator), then validate on fresh seeds 6-10, reporting the
original run as the first result.

## 3. Step 5: anomaly detection (seeds 11-15)

Planted: conversion rate halves for the last 14 days in two campaigns (10 cases over 5 seeds).

| Check | Threshold | Result |
|---|---|---|
| Detected at watch level or better by the last mature day | >= 8 / 10 | **8 / 10** (exactly at the limit) |
| Detected at alert level | >= 5 / 10 | 7 / 10 |
| Median detection delay from true onset | <= 9 days | 4.5 days (delays 3, 3, 3, 4, 5, 5, 7, 9) |
| False-alarm alerts per seed (non-planted campaigns, all metrics) | <= 1.0 | 0.2 |
| False-alarm episodes per seed (watch + alert) | <= 3.0 | 1.0 |

- The M3 plan said "detect within 5 days". That was written without a power analysis: at ~40-60 clicks/day a
  halved rate gives z ~ 2 after 3 days, so a false-alarm-safe detector cannot promise it. The criteria above
  replaced it **before** any detector was built. Detection did occur within 5 days in 6 of the 8 detected cases,
  but that depends on luck.
- The two misses are low-traffic campaigns where chance muted the effect (on seed 42, one campaign's observed
  drop was 34% instead of the true 50%: evidence 3.4 against a watch bar of 4.7).
- Thresholds: conversion rate watch 4.7 / alert 6.7; CPC and spend (k = 0.5) watch 7.0 / alert 10.5, the
  conservative heavy-tailed calibration. No CPC/spend anomalies were planted, so only false-positive behaviour was tested for them.

## 4. Step 6: forecasting (seeds 21-25, true holdout)

130 series: account spend and sales (10) plus 12 campaigns x 2 metrics x 5 seeds (120).

| Check | Threshold | Result |
|---|---|---|
| Account: selected model no worse than seasonal-naive | >= 7 / 10 | **6 / 10 - fail** |
| Account: mean MAE improvement over seasonal-naive | >= 5% | 7.8% |
| Account: mean WAPE of the selected forecast | <= 15% | 12.0% |
| Campaign: no worse than seasonal-naive | >= 60% | 85% |
| Interval coverage, 80% nominal | 65-92% | 77% |
| No negative bounds; intervals contain the point | all | pass |

Model chosen across all 130 cases: ETS 91, gradient boosting 23, ridge 15, moving average 1 (baseline fallbacks: 1).

**Interpretation.** On average the selected models beat seasonal-naive, but four of ten account cases were
*worse*, as much as 28% worse. Two plausible causes (not confirmed): model choice from about seven overlapping
backtest folds is noisy; and a learned model won in 129 of 130 cases because seasonal-naive repeats last week's noise,
a weak bar - the smoothed 4-week seasonal mean may be a fairer benchmark. Because seeds 21-25 are now used, any
change (stricter margin, different benchmark, ensembling) needs fresh seeds.

## 5. Plan deviations, with reasons

| Plan said | What happened | Why |
|---|---|---|
| Wilson lower bound for opportunity ranking | Exact (Clopper-Pearson) bound | A test showed Wilson ranks 1 order from 1 click above 20 from 100 |
| Detect the anomaly within 5 days | Median <= 9 days, power-based criteria | The 5-day goal is statistically unreachable at these volumes |
| ~5 backtest folds | 7 folds on 117 mature days (4 on 100) | Folds are determined by history length (60 training days, weekly steps) |
| `forecast_runs` migration `0003` | Done, plus `clock_timestamp()` ordering | `now()` ties within a transaction; "latest" was arbitrary (found by a flaky test) |

## 6. Not yet verified

- Query timing for the analytics reader (dashboard under 1 s on the 646k-row seed) and the `EXPLAIN` index review.
- The Docker Compose stack (Docker is not installed on the development machine).
- Everything on real data.
