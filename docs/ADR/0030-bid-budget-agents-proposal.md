# ADR 0030: Bid and Budget agents - options and recommendation

Status: **PROPOSED, not accepted. Needs an owner decision. No code accompanies this ADR.**

## Context
The original plan (ADR 0006, 0018, `docs/AGENT_ARCHITECTURE.md`) had LLM "Bid" and "Budget" specialists that produce recommendations, a human
approval interrupt, and a Postgres checkpointer. What was built instead (ADR 0022, 0028):
- Bid, budget and negative-keyword proposals are **deterministic calculators** (`application/optimization/generate.py`) with guardrails (cap 20%,
  positive values, expiry). They are created by `POST /api/v1/recommendations/generate` and decided by an admin, hash-bound, in the Approval Center.
- The assistant can **read and explain** them (`get_recommendations`, `recommendation_review`) and has **no write tool**.

Project rules that bound the options (`CLAUDE.md`): business calculations are deterministic Python and LLMs only reason and explain; no ad change
without explicit human approval; execution stays disabled.

Evidence from the 2026-11-15 live evaluations (docs/ASSISTANT.md): the **default single-engine assistant failed the zero-slack action-request
criterion on 1 of 4 golden questions** (it gave data but did not say it cannot make changes) and failed 2 of 3 out-of-scope recommendation
questions (predictive wording). The agent graph passed all nine recommendation questions and every older criterion.

## What "a Bid/Budget agent" could mean
| | Option | What the LLM does | New risk |
|---|---|---|---|
| A | **Keep deterministic; the calculators are the Bid/Budget logic** | explains proposals (done) | none; the "agents" in the plan stay unbuilt by design |
| B | **A gated `propose` tool** | calls one tool that runs the *existing* generator (optionally with a bounded `period` and `target_acos_pct`) and stores the results as **pending**; it never chooses or edits a value | the assistant gains a write path, so an injected or mistaken instruction can create pending proposals (idempotent: same content gives the same id; they only wait for a human) |
| C | **Approval inside the chat** (LangGraph interrupt + Postgres checkpointer) | the graph pauses and a person confirms in the conversation | approval by chat text is weaker than the hash-bound UI approval; adds a checkpoint schema and a long-lived state; the largest change |
| D | **An LLM that proposes values** | would set bids or budgets | **Rejected**: breaks the deterministic-calculation rule and cannot be validated by the faithfulness check |

## Recommendation
1. **First fix the assistant's refusal reliability, before any write-capable tool exists.** The evidence above says the default engine is not yet
   dependable on "say you cannot make changes". Options: make the graph engine the default (it passed everything), and/or tighten the system
   prompt's action-request rule. Either needs a **fresh, pre-registered question set** to evaluate (the existing 29 + 12 + 9 are now used for diagnosis).
2. **Then, if you want conversational proposal generation, build Option B only**, behind a setting that is **off by default**, with these limits:
   - the tool takes only bounded, validated arguments (period within the data, target ACOS 1-1000), no values, ids or tenant;
   - it requires the analyst role and is rate limited like questions; every call is audited (`recommendations.generated`, actor `assistant:<subject>`);
   - it creates `pending` proposals only; approval, rejection and execution stay exclusively in the Approval Center and the (blocked) execute route;
   - a test fails if any assistant path can change a status, create an approval or an execution attempt;
   - a pre-registered live evaluation (including prompt-injection attempts planted in keyword text asking it to "generate" or "approve") must pass
     before the setting is ever enabled.
3. **Do not build Option C now.** Hash-bound approval in the UI is the stronger control; revisit only if a real need for in-chat approval appears.
4. Option D stays rejected.

## Consequences of each choice
- Choosing **A** closes the topic at no cost; the plan documents are updated to say the Bid/Budget "agents" are the calculators.
- Choosing **B after step 1** adds roughly 2 days of work plus one pre-registered evaluation (about $0.1) and a new audit action.
- Nothing here changes the approval workflow, the guardrails or the disabled execution gate.
