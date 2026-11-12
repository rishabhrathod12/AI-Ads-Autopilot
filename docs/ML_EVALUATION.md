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

### 2b. Re-scoring on fresh seeds 6-10 with restated truth (criteria pre-registered in `c2a0f06`)

Rules unchanged; only the ground truth was restated (see `test_keyword_findings_eval_v2.py`). Seeds 1-5 above remain the first result.

| Check (mean over 5 seeds) | Threshold | Result |
|---|---|---|
| Wasted targets: precision of "confirmed" vs targets converting below half their campaign's rate | >= 0.85 | 0.90 - pass |
| Wasted targets: recall vs planted zero-rate targets (unchanged) | >= 0.75 | 0.93 - pass |
| Negative terms: precision of "confirmed" (unchanged) | >= 0.85 | **0.77 - fail** |
| Negative terms: recall over testable planted terms | >= 0.70 | 0.94 - pass |
| Opportunities: recall / precision of "strong" | >= 0.80 / 0.80 | 0.89 / 0.94 - pass |

- Against the original narrow truth, wasted precision is 0.72 on these seeds, so the restatement is what makes it pass; the
  rules did not improve. Original-truth negative recall is 0.58 (testable subset: 0.94 over about 19 terms per seed).
- Negative precision was 0.86 on seeds 1-5 and 0.77 here, so it was marginal before. Cause not diagnosed.
  Recorded as a strict known failure; changing it needs another fresh seed set and an owner decision.
- The restated criteria were written after seeing seeds 1-5, so this is a validation of a post-hoc definition, not a clean pre-registration.

**Diagnosis of the negative-precision failure (post hoc; scratch script, not a scored run).** Pooled over seeds 1-10, 140
terms were "confirmed" and 26 are not in the planted set (pooled precision 0.81). Every one of the 26 has a true
conversion rate **below its campaign's mean** (16 below half of it, 10 between half and the mean; none at or above the
mean, none exactly zero). They are ordinary terms with a genuinely weak rate that booked zero orders in 30-110 clicks, the
same pattern as the wasted-target "false positives" in section 2. The detector is doing what its rule says (zero orders
would be <= 5% likely at the campaign's *average* rate); the planted truth counts only terms generated as irrelevant. So the
miss is mostly a truth-definition gap, not evidence the rule is wrong, but it also means a "confirmed" negative is a
*weak-converter* flag, not proof the term is irrelevant. Nothing was changed. Scoring "below half the campaign's rate" as
true would put pooled precision at about 0.93 only if applied to a fresh seed set; that has not been run.

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

### 4b. Account forecast re-score on fresh seeds 26-40 (pre-registered in `47ef3ae`; rule unchanged)

Diagnostic first, on the seen seeds 21-25 (account cases, not scored): benchmarking selection against the better of
seasonal-naive and the 4-week seasonal mean gave 5/10 not worse (mean improvement 0%); a 10% margin gave 6/10 (+7.8%); both
together gave 4/10 (-9.8%). The weak-benchmark hypothesis was **not supported** and the rule was not changed.

Then the unchanged rule on 15 fresh seeds (30 account cases):

| Check | Threshold | Result |
|---|---|---|
| Selected model no worse than seasonal-naive | >= 70% (21 of 30) | **19 of 30 (63%) - fail** |
| Mean MAE improvement over seasonal-naive | >= 5% | **-6.7% - fail** |
| Mean WAPE | <= 15% | 12.5% - pass |
| No negative bounds, intervals contain the point | all | pass |

Models chosen: ETS 20, ridge 9, gradient boosting 1, no baseline fallbacks. A few large losses dominate the mean (for example
seed 27 spend: MAE 58k against 15k for seasonal-naive), and several cases where ridge was chosen lost heavily.

**Conclusion.** The first run's +7.8% was luck on a small sample. The fair reading is: the selected model is **not**
reliably better than "same weekday last week", and on average slightly worse, although its WAPE is acceptable. The
"beat the baseline or say so" selection rule does not deliver that promise at ~117 days of history. Not diagnosed further:
the models' errors are large in a few series, and a learned model won in every case, so the backtest-based gate is not
filtering anything. Options for an owner decision (each needs fresh seeds): ship seasonal-naive/4-week mean as the default
forecast and show the learned models as experimental; require a much larger margin or a stability check; or average models.

### 4c. Shipped default after 4b (owner decision, 2026-11-12)

The default forecast is now seasonal-naive; learned models are backtested and reported as experimental and are opt-in
(`allow_learned=True`, ADR 0013 update). The held-out forecast evaluations still score the learned selection rule, and
their failures remain recorded as strict known failures. The change removes the risk of shipping a forecast worse than
the baseline; it does not make the forecast more accurate.

## 4d. Independent evaluations (M3 close-out step 4; criteria pre-registered in `1f83863`, run once)

**Forecast on a differently-built generator** (`tests/evals/independent_series.py`: level drift and steps, evolving weekly shape, promotions and outliers that can fall in the holdout, wandering ROAS; seeds 101-120, 40 cases, 14-day holdout). Spent seeds: not reusable.

| Model | MAE | RMSE | MAPE | WAPE | Not worse than seasonal-naive | Mean MAE gain |
|---|---|---|---|---|---|---|
| naive | 2.15M | 2.60M | 0.304 | 0.299 | 57% | -11.9% |
| moving average 7 | 1.87M | 2.30M | 0.267 | 0.242 | 90% | +13.6% |
| **seasonal-naive (shipped default)** | 2.23M | 3.19M | 0.308 | **0.294** | (reference) | 0 |
| seasonal mean 4 | 1.63M | 2.12M | 0.236 | 0.222 | 80% | +15.2% |
| ETS | 1.78M | 2.20M | 0.252 | 0.233 | 80% | +15.1% |
| ridge | 1.82M | 2.24M | 0.255 | 0.238 | 75% | +10.7% |
| gradient boosting | 1.81M | 2.28M | 0.255 | 0.235 | 72% | +12.5% |
| opt-in selection rule | 1.81M | 2.23M | 0.248 | 0.230 | 85% | +17.1% |

| Criterion | Bar | Result |
|---|---|---|
| S1 shipped default mean WAPE | <= 0.25 | **0.294 - fail** (recorded as strict known failure) |
| S2 shipped default 80% interval coverage | 0.65-0.92 | 0.779 - pass |
| S3 sane intervals | all | pass |
| R1 opt-in selection: no worse in >= 70% and gain >= 5% | | 85%, +17.1% - pass |
| R2 seasonal mean 4: same bars | | 80%, +15.2% - pass |

**Reading.** This contradicts the mock-generator result (4b), where learned models were *worse* than seasonal-naive. On this source
the shipped seasonal-naive default is the least accurate of the simple baselines, and the plain 4-week seasonal mean (no learning) is
as good as any learned model. Two synthetic sources disagree, so neither is evidence about real accounts; the differences come from
how much noise and level change each generator contains. The only supported conclusion is that seasonal-naive is a noisy default
and `seasonal_mean_4` deserves a pre-registered comparison. **The default was not changed**; that needs an owner decision and fresh seeds.
Caveat: the generator's design choices (promotions, outliers, lognormal noise) penalise "repeat last week" by construction.

**Keyword findings against economic truth** (seeds 41-45; truth = true ACOS = observed CPC / (latent rate x account AOV) above an *assumed*
30% break-even; mock data has no margins). Labels are independent of the generator's planted categories; the data is not.

| Check | Bar | 30% | 25% | 40% |
|---|---|---|---|---|
| K1 confirmed negative terms truly unprofitable (69 terms over 5 seeds) | >= 0.85 | **0.934 - pass** | 0.934 | 0.898 |
| K2 confirmed wasted targets truly unprofitable (166 targets) | >= 0.85 | **0.821 - fail** | 0.878 | 0.777 |

K2 per-seed range 0.70-0.89; it is sensitive to the break-even assumption, and the pass/fail verdict would flip at 25%.
Reading: judged by money rather than by the generator's labels, confirmed negative terms are mostly genuinely unprofitable, which
supports the earlier diagnosis that the 0.77 "negative precision" failure was a label-definition gap. Confirmed wasted targets are
less reliable (about 1 in 5 is not unprofitable at 30%). Terms or targets below 15 clicks are not scored.

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

## 7. Known limitations (M3 close-out, 2026-11-12)

Recorded, not fixed; none is hidden by a changed threshold.

1. **No real-account evidence.** Every score comes from one synthetic generator that also planted the scenarios. They show the code behaves as designed, not accuracy on Amazon data.
2. **Negative-term "confirmed" means weak converter.** Pooled precision on seeds 1-10 is 0.81 against the planted truth; seeds 6-10 scored 0.77 vs 0.85 (fail). All false positives convert below their campaign's mean. A confirmed negative is a candidate for human review, never proof the term is irrelevant.
3. **Restated keyword truth is post hoc.** The restated wasted-target criterion was written after seeing seeds 1-5; its pass on seeds 6-10 validates a definition, not the rules.
4. **Forecast.** Learned models are not shown to beat seasonal-naive (63% not worse, mean -6.7% on seeds 26-40). The shipped default is seasonal-naive; its accuracy is that of "same weekday last week" (account WAPE ~12%, synthetic data).
5. **Anomaly detection** passes exactly at its limit (8/10) on one planted pattern (conversion-rate halving); CPC and spend anomalies were never planted, only false-positive behaviour was tested.
6. **Unverified here:** analytics query timing, `EXPLAIN` index review, the Docker Compose stack, and (until a Postgres with pgvector is available) the 61 database-backed tests including RLS isolation.
7. **Seeds already used** (1-15, 21-40) cannot be reused for new scored claims.
8. **Independent evaluations (4d):** wasted-target "confirmed" findings are economically unprofitable in 82% of cases at an assumed 30% break-even (bar 0.85); the shipped seasonal-naive default has WAPE 0.294 on the independent series (bar 0.25), and simple smoothing baselines beat it there. The two synthetic sources disagree on whether learned models help; no real-account evidence.
