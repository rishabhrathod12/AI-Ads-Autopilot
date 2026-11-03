# ADR 0018: M5 agent graph scope

Status: Proposed (M5)

## Context
M4 delivered a single tool-calling assistant with a code-level faithfulness validator (ADR 0015). ADR 0006 chose
LangGraph with a Supervisor, specialists and a Validator. Bid and Budget specialists produce recommendations that
need the M6 approval workflow, and real authentication does not exist yet.

## Decision
- M5 builds the graph with a Supervisor, Campaign Analysis, Keyword Intelligence, Performance Prediction and PPC
  Reporting specialists. Bid Optimization and Budget Allocation move to M6.
- The M4 `faithfulness` validator is reused unchanged as the Validator node. Specialists wrap the existing tested
  analytics tools; all remain read-only and tenant-bound.
- Typed `AgentState` with step, token and tool-call budgets; Postgres checkpointer; run traces persisted.
- The single-agent path stays as a fallback behind the same chat API.
- Evaluation is pre-registered and committed before any live run, and compared against the M4 held-out baseline
  (docs/ASSISTANT.md). The graph must not do worse on faithfulness; routing accuracy gets its own fixed criterion.

## Consequences
- M5 is read-only: no recommendations, approvals or execution.
- Adds the LangGraph dependency and a checkpoint schema migration.
- A multi-agent graph costs more tokens per question; the evaluation records cost against the baseline.
