# Mock Advertising Data (M2)

> **Everything described here is synthetic and labeled `MOCK`.** It demonstrates the platform's *method*.
> It says nothing about real Amazon accounts and must never be presented as live data.

## Selecting the provider
`ADS_PROVIDER=mock` (default) uses `MockAdvertisingDataProvider`. `ADS_PROVIDER=amazon` raises
`ProviderNotAvailableError` until M9; there is no simulated Amazon connectivity. Application code depends only
on the `AdvertisingDataProvider` port (`app/application/ports/ads_provider.py`).

## Reproducibility
Fixed seed => identical dataset (`MOCK_SEED`, default 42). History length is `MOCK_HISTORY_DAYS` (default **120**,
minimum 30). The last day is yesterday unless `end_date` is given.

## Model
Bottom-up: latent behaviour is drawn per **search term**; targets, ad groups and campaigns are exact sums of their
terms, so every level agrees (tested). Default size: 12 campaigns, 36 ad groups, ~1,700 targets, ~4,300 search terms,
~640k daily metric rows.

| Effect | Modelled as |
|---|---|
| Traffic long tail | Zipf-like keyword weights; many keywords have too little data to act on |
| Day-of-week and trend | Weekday multipliers, slight upward trend |
| Promo spike | 2-day demand and conversion boost, 45 days before the end |
| Match types | Exact converts better, broad worse |
| Bid vs CPC | Bid always >= average CPC |
| Attribution lag | Last 3 days under-report orders/sales and are flagged `is_mature=False` (7-day window) |
| Money | Integer paise (INR) |

## Planted scenarios (ground truth for later milestones)
`MockAdvertisingDataProvider.ground_truth` lists what was planted, computed from the generated data:

| Scenario | Meaning | Used by |
|---|---|---|
| `wasted_target_ids` | Real traffic (>=15 clicks), zero orders | M3 wasted-spend detection, M6 bid cuts |
| `negative_term_ids` | Irrelevant search terms (>=10 clicks, zero orders) | M6 negative-keyword recommendations |
| `opportunity_term_ids` | Converting auto-campaign terms (>=3 orders) | M6 keyword promotion |
| `high_acos_campaign_ids` | Bids too high, conversion too low (ACOS ~50%) | M3 health status, M6 recommendations |
| `budget_capped_campaign_ids` | Efficient campaign throttled by a small budget | M6 budget reallocation |
| `anomaly_campaign_ids` + `anomaly_start` | Conversion halves for the last 14 days | "Why did my ACOS increase?" |

## Syncing into the database
`python -m app.cli seed-mock [--org-id UUID] [--seed N] [--days N]` pulls from the provider through `SyncService` and
upserts inside a tenant-scoped transaction (RLS). Re-running with the same `--org-id` is idempotent: internal ids are
deterministic (`uuid5(org, kind:external_id)`). Each ingested report is recorded in `report_runs` (source, date range,
rows ingested/quarantined). The full 120-day seed (~646k rows) measured 2m46s on a Windows dev machine, dominated by PostgreSQL
constraint checks (metrics are bulk-loaded via COPY into a staging table, then upserted).

## Validation and quarantine
Domain models reject impossible data at construction (clicks > impressions, orders > clicks, spend without clicks, ...).
`SyncService` additionally quarantines (reports, counts on the run, never persists or silently fixes): records whose
`data_source` differs from the provider, records with unknown parents, metrics for unknown entities, out-of-range
dates and duplicate rows. The database repeats the key invariants as CHECK constraints.

## Limitations
- Distributions are plausible, not calibrated to real Amazon.in accounts.
- Sponsored Products only; search-term and keyword metrics are daily, as a real report would be requested.
- The Ads API shape is modelled from public documentation knowledge; M9 may need small adjustments, guarded by the
  provider contract tests (`tests/contract`).
