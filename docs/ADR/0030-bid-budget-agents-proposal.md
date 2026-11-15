# ADR 0030: Bid and Budget agents - options and recommendation

Status: **ACCEPTED 2026-11-15: Option A (the owner chose to close this).** Options B, C and D are not built and not planned. No code accompanies this ADR.

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

## Update 2026-11-15: step 1 result (still PROPOSED)
Step 1 (refusal reliability) was run as pre-registered (docs/ASSISTANT.md, "Refusal reliability"). **It did not reach the bar.** The default single-engine
assistant refused 10/12 (Set 1) and 9/12 (Set 2, after the prompt was tightened) action requests, against a no-slack bar of 12/12; the graph engine refused
24/24 but failed the out-of-scope criterion (4/6 on both sets) with safe but unhelpful `unverified` answers and one wrong "no data" answer. The recommendation
above therefore stands: **no write-capable tool (Option B) should be built until action refusal is reliable at whichever engine is the default.** The open choice
is how to get there: graph as the default, a deterministic code-level guard for action requests, or accepting the current behaviour (the assistant has no write
path, so a missed refusal misleads but changes nothing). Each needs a new pre-registered Set 3.

## Update 2026-11-15 (later): a code-level guard met the refusal bar (still PROPOSED)
The owner chose the deterministic guard (`assistant/action_guard.py`). On the pre-registered, fresh Set 3 both engines refused 12/12 action requests,
the default single engine also met every other criterion, at $0.04-0.06 per engine (docs/ASSISTANT.md, "Round 3"). **Limitation:** the guard is a hand-written
list built by the same author who wrote Set 3, so it is shown to work on wordings that author anticipated, not on unanticipated ones; unflagged requests
fall back to the model's own 9-10 of 12. The precondition in this ADR ("no write-capable tool until action refusal is reliable at the default engine") is
therefore **met for the tested wordings, not proven in general**. Before Option B is built: a Set 4 of requests written by someone else (offline check
first, since it needs no model), and Option B itself would still be gated, off by default, with its own pre-registered evaluation including
prompt-injection tests. Nothing has been built for Option B.

## Decision (2026-11-15): Option A, close it
The owner closed this ADR as **Option A**: the deterministic calculators (`application/optimization/generate.py`) *are* the Bid and Budget logic. There are no
LLM "Bid" or "Budget" agents, the assistant keeps **no write tool** (it reads and explains proposals: ADR 0028; it refuses change requests: the guard in
`assistant/action_guard.py`), and approval stays exclusively in the Approval Center. This follows the project rule that business calculations are
deterministic and LLMs only reason and explain.

What this closes: the "Bid Optimization" and "Budget Allocation" LLM specialists, the Postgres checkpointer, and an in-chat approval interrupt that
ADR 0006, ADR 0018 and `docs/AGENT_ARCHITECTURE.md` had planned. They are not deferred; they are dropped.

What it does not close: the refusal guard still lacks independent validation (try wordings with `python -m app.cli check-guard`), and nothing here touches
the approval workflow, the guardrails or the disabled execution gate.

Reversible: if conversational proposal generation is wanted later, Option B above (a gated, off-by-default `propose` tool that only runs the existing
generator and creates `pending` proposals) remains the described path; it would need a new ADR, a Set 4 of independently written requests, and its own
pre-registered evaluation including prompt-injection tests.
