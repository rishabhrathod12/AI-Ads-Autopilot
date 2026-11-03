# ADR 0016: Provider-independent LLM port with enforced cost controls

Status: Accepted (M4). Builds on ADR 0010.

## Context
The assistant spends real money and talks to an external provider. Keys, prompts and spend must be controlled in
code, and the application must not depend on one vendor.

## Decision
- `LLMClient` and `EmbeddingClient` are ports in the application layer with typed errors (not configured, auth, bad
  request, rate limited, unavailable, bad response, budget exceeded). The OpenAI adapters use httpx directly.
- Decorators compose: provider adapter, then retries with timeout, then a per-tenant budget wrapper. Chat and
  embeddings share one spend ledger.
- **Spend caps are enforced before the call**: if the worst-case cost (estimated input plus the full output
  allowance) could breach the daily or monthly cap, the call is refused. Actual cost is recorded after. The ledger is
  a database table (`llm_usage`), so caps survive restarts and span processes. Known limit: two simultaneous calls
  can both pass the check, overshooting by about one call.
- Prices are required configuration (never guessed), so the cap is real. Providers default to `none`: nothing is sent
  anywhere until configured, and startup validation rejects half-configured setups.
- Redaction removes keys, tokens and email addresses from questions, logs and stored text. The API key is a
  `SecretStr`, never logged, and never in a frontend bundle; error messages carry status and code, never the
  provider's free text.
- Errors from the provider become statuses in the answer, not exceptions to the seller.

## Consequences
- A runaway loop cannot spend more than the cap (the evaluation also sets its own ceiling).
- Estimating tokens is approximate and conservative; real usage comes from the provider's response.
- Model choice is configuration. The adapter sends `max_completion_tokens` and an optional temperature; reasoning
  models that reject temperature need `temperature=None`, so `gpt-4.1-mini` is the verified choice for now.
