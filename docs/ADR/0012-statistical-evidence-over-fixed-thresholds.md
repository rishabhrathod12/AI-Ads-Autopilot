# ADR 0012: Statistical evidence instead of fixed thresholds, evaluated on held-out data

Status: Accepted (implemented in M3)

## Context
Click, order and spend counts per keyword or campaign are small and noisy. "Flag any keyword with 20 clicks and no
orders" is too strict for low-converting categories and too lax for high-converting ones. Detection rules also need
an honest accuracy check that does not reward tuning to the answer.

## Decision
- **Waste and underperformance** are judged by the exact binomial probability of seeing so few orders by luck at the
  campaign's own conversion rate (confirmed <= 5%, watch <= 20%). Items under 10 clicks are counted, not flagged.
- **Opportunity ranking** uses the exact (Clopper-Pearson) 95% lower bound of the conversion rate. The Wilson bound
  was tried first and rejected: it ranks 1 order from 1 click above 20 from 100.
- **Anomalies** use CUSUM (Bernoulli likelihood for conversion rate; robust Gaussian for CPC and spend). Thresholds
  are calibrated by Monte Carlo simulation of *no change*, not from planted scenarios.
- **Evaluation** criteria are pre-registered, run once on held-out seeds, and failures are recorded rather than
  adjusted. Changing a rule or criterion needs fresh seeds and an owner decision.
- Findings are evidence, not recommendations; M6 produces proposals that need human approval.

## Consequences
Each finding states how sure it is and why. Statistics are floats while money stays `Decimal`. Some real effects are
undetectable at low traffic (reported honestly, e.g. a halved conversion rate takes about a week to confirm at
~50 clicks/day). Three pre-registered checks failed on first run (see ML_EVALUATION.md); they are marked as strict
known failures with diagnoses.
