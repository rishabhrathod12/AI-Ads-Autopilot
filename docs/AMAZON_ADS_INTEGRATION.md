# Amazon Ads API integration (M9)

**Status: the connection layer exists; the Amazon-specific part does not.** No real Amazon endpoint is called anywhere in
this repository, no Amazon credentials exist, and API access has not been requested or granted. `ADS_PROVIDER=mock` and
`AMAZON_ADS_ENABLED=false` are the defaults; `AD_EXECUTION_ENABLED` is `false` and cannot be enabled (ADR 0007, 0025).

## What was researched, and what could not be
The official Amazon Ads documentation renders in the browser, so automated reading returned only a page title. Nothing below
was taken from memory.

**Confirmed from published sources (2026-11-12):**
- Login with Amazon (LWA): the refresh-token exchange is a POST with `grant_type=refresh_token`, the refresh token and client
  credentials; the response has `access_token` and `expires_in` (about an hour). Refresh tokens are issued only when a client
  secret is used and stay valid until the user revokes the app. LWA has regional endpoints (NA, EU, FE). These are general LWA
  pages, some years old, not Ads-specific.
- Ads API rate limits are dynamic and load-based; clients should expect 429 responses.
- API access policy: limited to companies that do not operate a significant ecommerce business, a demand-side ad platform or an
  ad network. **Whether a seller-facing tool qualifies, and how a seller reaches their own account's data, is unconfirmed.**
- A 2026 discussion says Amazon Ads API **v1** is the latest unified API and replaced the product-specific v2/v3 APIs, so older
  knowledge of v3 paths and report names may be obsolete.

**Not confirmed (do not assume):** every endpoint path; the required request headers and their exact names; the LWA token URL for
a region; regional API base URLs; how profiles, campaigns, ad groups, targets and search terms are listed; how reports are
requested and downloaded; report type ids, groupBy values and metric names; attribution windows and restatement timing;
whether `Retry-After` is sent; how access is applied for and approved.

## What was built (endpoint-agnostic, disabled by default, 83 unit tests)
| Piece | Location | Notes |
|---|---|---|
| Token manager | `application/ads_api/token_manager.py` | Caches the access token in memory, refreshes 60 s early, one refresh at a time (concurrent callers share it), failures are not cached, rotated refresh tokens are handed back for storage |
| Retry and rate limiting | `application/ads_api/retry.py` | Exponential backoff with jitter; honors `Retry-After` (seconds or HTTP-date) when present; a wait longer than the cap is reported to the caller instead of slept; token-bucket limiter |
| LWA refresher | `infrastructure/ads_api/lwa.py` | The general LWA refresh exchange; the token URL is **configuration**; https only; no redirects; errors never include bodies or secrets |
| HTTP client | `infrastructure/ads_api/http_client.py` | Base URL from configuration (https); path allowlist (no host override, no `..`, no query in the path); one refresh and one retry on 401; 429/5xx/timeouts retried; no redirects; 10 MB cap; tokens, headers, query strings and bodies never logged; refuses to send until a `headers_factory` is supplied; `mutating=True` refused unless built with `mutations_allowed=True` (nothing does) |
| Encrypted token vault | `infrastructure/ads_api/token_cipher.py`, `vault.py`, `ports/credential_store.py` | AES-256-GCM, versioned keys (rotation keeps old ciphertexts readable), ciphertext bound to organization and connection so a copied row will not decrypt |
| Provider skeleton | `infrastructure/providers/amazon/provider.py` | Implements the provider port; every read raises `ProviderNotAvailableError` naming the missing mapping; `execute_change` always raises `ExecutionNotPermittedError`; **not wired into `build_provider`** |
| Configuration checks | `startup_checks.validate_amazon_ads_config` | `ADS_PROVIDER=amazon` and `AMAZON_ADS_ENABLED=true` must agree; credentials, https URLs, rate settings and encryption keys must all be present and valid |

Not built: a SQL credential store and its migration (it would add unverifiable database code before the mapping exists), the OAuth
authorization-code web flow (consent redirect, `state` and PKCE; needs the verified authorization URL and scopes and a frontend
login), the endpoint mapping, report download (pre-signed URLs need a host allowlist), data synchronization against real
responses, and any execution path.

## To finish M9 (in order, each needs the owner)
1. **Confirm eligibility** with Amazon and obtain Ads API access and a client id/secret (original open decision #1). Until then
   everything above stays dark.
2. **Supply the verified documentation** (rendered pages, PDF export or the official OpenAPI/Postman files): authorization and
   headers, regional URLs, profiles, reporting, Sponsored Products objects, rate limits.
3. **Implement the header scheme** as the `headers_factory`, from those documents, and unit-test it against recorded examples.
4. **Write the mapping** in the provider: profiles -> `Profile`, campaigns/ad groups/targets/search terms -> the domain models, and
   reports -> `DailyMetrics`, including units (paise), the attribution window and the maturity rule. Add contract tests with
   recorded fixtures: the existing provider contract tests must pass for it.
5. **Add the SQL credential store** (migration, RLS, insert/update only) and verify it against a database.
6. **Build the OAuth authorization-code flow** with `state` and PKCE, and a frontend connect button.
7. **Run against a sandbox or a test account first, read-only.** Compare totals with Amazon's console before trusting any number.
8. Only then, separately: **execution** of approved changes. It needs its own design, ADR and owner approval (rollback values,
   dry run, idempotency, the approval hash re-check, a non-mock provider, and the `ExecutionPrerequisites` flags set honestly).

## Security notes
- Refresh tokens are encrypted at rest with keys that live outside the database (`TOKEN_ENCRYPTION_KEYS`); access tokens live in
  memory only. Neither appears in logs, errors or `repr`.
- The client secret and encryption keys are `SecretStr` settings and are never logged.
- Mock data stays labeled `MOCK`; records from this provider would be labeled `AMAZON_ADS_API`. Do not claim a real integration
  until step 7 is complete.
