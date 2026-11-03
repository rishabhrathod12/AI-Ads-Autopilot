# ADR 0015: Evidence contract and a code-level faithfulness validator

Status: Accepted (M4)

## Context
The assistant must never present an invented figure as fact. Telling a model "do not make up numbers" reduces but
does not remove the problem, and tool output contains seller-controlled text that could try to instruct the model.

## Decision
- Every tool result is an `Evidence` record with provenance (tool, arguments, data sources, MOCK flag, as-of time,
  last mature day, period). The model sees provenance first and the data second.
- Seller text and retrieved passages are untrusted DATA: control characters stripped, length capped, instruction-like
  phrases flagged (`possible_prompt_injection`), and the notice repeated in every result. Tools are read-only and
  take no tenant parameter, which bounds the damage of any successful injection.
- Money is offered as both `*_paise` and a computed `*_rupees`, so the model never converts.
- The model must finish with a structured `final_answer`: claims, each citing evidence ids.
- A non-LLM validator checks each number and date in a claim against the cited evidence: same unit class, same sign
  if one is written, rounded half-up or truncated to the digits stated, list lengths and tool arguments count, bare
  whole numbers must match whole-number facts exactly. Figures the model derived itself are unsupported by design.
- Failure policy: one correction round with specific, number-free feedback; then drop unsupported claims (`partial`)
  or show no figures (`unverified`).
- Numbers quoted from a cited reference passage are accepted without unit matching (ADR 0017).

## Consequences
- Strict, so an honest rounding style the rules do not recognise is rejected and retried; tuning is by tests.
- It checks magnitudes, not meaning. A real number with a wrong label passes (observed in the golden evaluation,
  docs/ASSISTANT.md). Semantic checks need an evaluation set, not more rules.
- The validator is deterministic and tested on its own with adversarial cases, independent of any model.
