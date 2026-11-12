# ADR 0022: Approval workflow, hash-bound approval, deterministic recommendation calculators

Status: Accepted (implemented 2026-11-12, pending owner review; database layer verified on PostgreSQL 18.6 only, see Consequences)

## Context
M6 turns analytics into proposals a human can act on, without any path that changes an advertising account.
Requirements: lifecycle, pending approval, approve/reject, hash-bound approval, expiry, tenant ownership, execution
safeguards, idempotency, complete audit history. ADR 0018 deferred the Bid and Budget specialists to this milestone.

## Decision
- **Proposals are computed by deterministic Python, not by agents.** `application/optimization/generate.py` derives
  negative-keyword, bid and budget proposals from the existing analytics. Each carries the exact inputs it used
  (`evidence`), a rationale, confidence and a *direction-only* expected impact (no invented money figures). The Bid and
  Budget "agents" of the original design are therefore calculators, not LLM specialists; this avoids a model in the
  numeric path (ADR 0005) and keeps proposals reproducible. Wrapping them as read-only graph specialists is deferred.
- **Guardrails** (`domain/recommendations.py`): bid and budget changes are capped at +/-20% (clamped after rounding; a
  property test found rounding could exceed the cap), values must be positive, expiry after creation. A proposal that
  breaks one is dropped and counted, never emitted. Bid proposals need >= 30 clicks, >= 3 orders and a >= 5% change.
- **Lifecycle:** `pending -> approved | rejected | expired`; nothing leaves a terminal state. `next_status` is the only
  definition. Expiry (7 days) is recorded as a status change and an audit entry when someone tries to decide late.
- **Hash-bound approval:** `content_hash` = SHA-256 of the canonical JSON of what is proposed (organization, type, entity,
  current and proposed values, evidence, data source; not id, status or timestamps). The approver must send the hash they
  reviewed; it is compared with a recomputation from the stored content, so a tampered row cannot be approved with its own
  stale hash. The hash is stored on the approval record.
- **Idempotency:** proposals have deterministic ids (`uuid5(org, hash)`) and a unique `(organization_id, content_hash)`,
  so generating twice stores nothing new. Repeating the *identical* decision by the same person returns the original
  record (`replayed: true`) and writes nothing; any other repeat is 409. The database allows one `approval_records` row
  per recommendation.
- **Atomicity:** status change (compare-and-set on `pending`), approval record and audit entry are written in one
  transaction. Errors that follow writes (recorded expiry, blocked execution) are *returned* as 409 responses, because
  raising would roll back the record explaining the refusal.
- **Roles:** viewers read; analysts generate; admin and owner approve, reject, request execution and read audit/executions.
- **Execution gate:** `POST .../execute` records an `execution_attempts` row and an audit entry, then answers 409. Reasons,
  checked in order: `execution_disabled`, `not_approved`, `approval_expired` (approval valid 24 h), `content_changed_since_approval`,
  `no_execution_provider`. **No code path calls an advertising provider.** `AD_EXECUTION_ENABLED` stays false and startup
  still refuses to enable it (ADR 0007); the table accepts only status `blocked`.
- **Immutability in the database:** the app role may INSERT and SELECT approvals, execution attempts and audit rows, and may
  UPDATE only `recommendations.status`; RLS (forced) isolates every table by organization.

## Consequences
- **Verified 2026-11-12** on PostgreSQL 18.6 with pgvector 0.8.7 (a throwaway local cluster), not yet on PostgreSQL 16 (the CI image): migration `0006` up and down; the 7 store contract tests; and a 33-check end-to-end run against the real API (roles, hash binding, idempotent replay, expiry and blocked execution still committed when the response is a 409, cross-tenant isolation through the API, and the app role refused when changing approvals, audit rows or proposal content).
- No automatic expiry job: an unreviewed recommendation only becomes `expired` when someone tries to decide it (reads show
  `pending` with a past `expires_at`; the UI must treat those as expired).
- No self-approval restriction, no two-person rule, no approval comment requirement; approver identity is the JWT subject.
- Bid/budget proposals do not model interactions (a bid change and a budget change on the same campaign are independent).
- The Postgres LangGraph checkpointer and an approval interrupt in the graph are not built.
- The frontend has no approval screens.
- Real execution remains M9 and needs: a provider implementation, a non-mock provider, rollback values, and a new ADR.
