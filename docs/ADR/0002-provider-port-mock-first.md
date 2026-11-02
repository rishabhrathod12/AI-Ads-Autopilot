# ADR 0002: Provider port and mock-first strategy

Status: Accepted (owner approved M0, 2026-11-02)

## Context
No Amazon Ads API access exists; fake Amazon connectivity is forbidden.

## Decision
Define AdvertisingDataProvider port; implement MockAdvertisingDataProvider now and AmazonAdsApiProvider in M9; select via ADS_PROVIDER config and DI. Interface shaped after the real API (async reports, lag, attribution windows).

## Consequences
Full platform buildable now. Contract tests prevent mock-specific assumptions. Mock execute always raises.
