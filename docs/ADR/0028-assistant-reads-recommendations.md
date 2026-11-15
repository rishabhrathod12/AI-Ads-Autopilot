# ADR 0028: The assistant can read and explain recommendations (read-only)

Status: Accepted by the owner on 2026-11-13 (chosen as the next task). Implemented and unit/SQL tested; **not evaluated with a
live model**.

## Context
Recommendations (bid, budget, negative keyword) are computed by fixed rules (ADR 0022) and decided by a person. The assistant had no
way to see them, so a seller could not ask "what is waiting for my approval?" or "why this bid change?". The wish was "AI-assisted"
recommendations; the safe, useful part of that is explanation, not generation.

## Decision
- New read-only tool `get_recommendations(status, limit)` and a new specialist `recommendation_review` (tools: that tool and
  `search_knowledge`). The tool is offered only when a recommendation store is configured, like `search_knowledge`.
- **Still no write path.** The tool lists stored proposals; the LLM cannot create, approve, reject, change or apply one, and nothing
  here proposes new bid, budget or keyword values. The system prompt and the supervisor prompt say so. The store given to the tool is
  used only for `list_recommendations` (a test fails if anything writes, transitions, approves, executes or audits).
- **Quotable figures.** What the tool returns is what the faithfulness validator can verify: type, effective status, confidence,
  entity label, proposed text, current and proposed value in paise (and rupees), `change_pct` (computed in Python because the model may
  not calculate), the rule name, the period, and the evidence numbers the rule used (stored percentages are strings; the tool converts
  them to real numbers). Stored prose (`rationale`, `expected_impact`) is **left out**: its numbers match no typed field and would
  fail validation. Internal ids and the content hash are not returned (nothing to replay).
- **Effective status.** A pending proposal past its expiry is reported as `expired`, with a count per status, using an injectable clock.
- **Untrusted text.** `entity_label` and `proposed_text` come from seller keywords, so they are sanitised, length-capped and injection-
  flagged like other seller-controlled keys (added to `UNTRUSTED_KEYS` and the notice shown to the model).
- Wiring: `ToolContext.recommendations` (the assistant router passes `SqlRecommendationStore` on the tenant session, so RLS applies;
  tested); the graph filters the tool out of a specialist's list when no store exists; the supervisor's plan limit now follows the
  number of specialists (it was hard-coded to 4, which broke the "run every specialist" fallback at 5).

## Consequences
- **The prompt changed, so the earlier live results did not describe it; they were re-run on 2026-11-15** (details and raw results in
  `docs/ASSISTANT.md`, last section). Golden 29: action-request safety criterion **failed 3/4** (C3: the assistant gave data but did not
  say it cannot make changes); held-out 12/12; agent graph all criteria pass; the new recommendation suite (9 questions) passes on the
  graph engine and **fails group D on the single engine (1/3)**, whose R7 answer used predictive language. Recorded as failures, no tuning.
  The unit tests (17 + 1 SQL) check mechanics, not model behaviour.
- The graph engine is not the default (`ASSISTANT_ENGINE=single`); the single assistant also gets the tool.
- Not done: asking the assistant to compare a proposal with an alternative, per-recommendation lookup by id, explaining past
  (approved) outcomes (no outcome data exists), Bid/Budget agents that *generate* proposals (a separate decision with its own approval
  design), a UI hint in the assistant panel.
