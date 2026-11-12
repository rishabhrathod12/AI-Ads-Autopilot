# ADR 0025: Endpoint-agnostic Amazon Ads connection layer (M9, part 1)

Status: Accepted (implemented 2026-11-12, pending owner review). The Amazon-specific mapping is NOT implemented.

## Context
M9 is the integration with the official Amazon Ads API. The official documentation could not be read automatically, API access
and eligibility are unconfirmed, and the instruction is not to invent endpoints or assume access. Some parts of an integration do
not depend on Amazon's specifics: token lifecycle, retry and rate limiting, safe HTTP handling, encrypted credential storage and
configuration checks.

## Decision
- Build only the parts that are endpoint-agnostic, behind configuration, disabled by default: token manager, retry/backoff and
  rate limiter, LWA refresh exchange (general LWA behavior; token URL is configuration), HTTP client, encrypted refresh-token
  vault, a provider skeleton, and configuration validation.
- **Do not guess** paths, headers, report names or metric names. The HTTP client refuses to send until a `headers_factory`
  written from verified documentation is supplied; the provider's reads raise `ProviderNotAvailableError`.
- **Safety defaults:** the provider is not wired into `build_provider`; `execute_change` always raises; the client refuses
  `mutating=True` unless built with `mutations_allowed=True`, which no code does; `ADS_PROVIDER=amazon` requires
  `AMAZON_ADS_ENABLED=true` (and all credentials, https URLs and valid encryption keys) or startup is refused; enabling execution
  still fails its own prerequisites check.
- **Transport hardening:** https only, redirects never followed, path allowlist (a test found `//host/path` slipping through the
  first version of the check), response size cap, no credentials, headers, queries or bodies in logs or error messages.
- **Credentials:** refresh tokens encrypted with AES-256-GCM, versioned keys, associated data binding organization and connection.
  No SQL store yet.

## Consequences
- Nothing in this ADR connects to Amazon. Completing M9 needs: eligibility and access, the verified documentation, the header
  scheme, the endpoint mapping, a SQL credential store, the OAuth consent flow, and read-only validation against a real or sandbox
  account (see docs/AMAZON_ADS_INTEGRATION.md).
- `Retry-After` handling follows the HTTP standard because Amazon's behavior was not confirmed; if Amazon signals throttling
  differently, the error mapping must change.
- The LWA exchange follows general LWA documentation (some pages are old); Ads-specific differences, if any, are unknown.
- Execution of approved changes remains out of scope and needs a separate ADR.
