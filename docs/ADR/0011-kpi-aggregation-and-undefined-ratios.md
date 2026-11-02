# ADR 0011: KPIs are aggregated before dividing, and undefined ratios are null

Status: Accepted (implemented in M3)

## Context
Averaging ratios across days or campaigns is the commonest PPC reporting error (a 10% and a 100% ACOS campaign
with spend 100 and 500 average to 55%; the true ACOS is 40%). Separately, a zero denominator forced to 0 reads as a
perfect result: spend with no sales would show an ACOS of 0.

## Decision
- All KPIs are computed from summed counts (`MetricTotals`); ratios are never averaged or derived from other ratios.
- A zero denominator yields `None` in the domain and `null` in the API, never 0 or infinity. A genuine zero
  numerator is a real zero (spend with no sales: ROAS 0, ACOS undefined).
- Ratios are exact `Decimal` (28 digits); rounding happens only at presentation. Money is integer paise.
- Recent days still inside the attribution window are flagged immature and excluded from any comparison or judgement.

## Consequences
Every consumer (API, agents, reports) shares one tested implementation. Clients must handle `null`. Sorting treats an
undefined ACOS as worst, not best. The rule is pinned by golden, property-based and mutation-checked tests.
