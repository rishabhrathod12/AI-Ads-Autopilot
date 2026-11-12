# ADR 0023: Log and audit hygiene

Status: Accepted (implemented 2026-11-12, pending owner review; SQL audit queries verified on PostgreSQL 18.6)

## Context
The platform handles seller data, JWTs, API keys and (later) OAuth tokens. Logs and audit rows are retained and read by
people who may not be allowed to see secrets, and free-text fields (exception messages, approval comments, assistant
questions) are exactly where credentials end up by accident.

## Decision
- **Redact by name and by value.** A field called `token`, `api_key`, `authorization` etc. is replaced; every string value
  anywhere in a log event (including nested data) is scrubbed for key-like tokens, bearer credentials, `key=value`
  secrets and email addresses (`redact_obj`, reusing the assistant's `redact`).
- **Exceptions are logged as data, not text dumps:** type, a scrubbed message truncated to 300 characters, and traceback
  frames as `file.py:line function` (no directories, no local variables). Clients still get a generic 500 with the
  request id.
- **One access-log line per request:** method, *route template* (not the raw path, so ids do not leak), status, duration,
  request id, organization id and role. Never the query string, headers or bodies. Unmatched paths log `unmatched`.
  Tenant context is carried on `request.state`, because middleware runs in a different context from the endpoint.
- **Every state-changing route writes an audit entry** (actor, organization, request id, before/after), in the same
  transaction as the change: forecast creation, assistant thread creation and questions (run id and question *length*,
  never the text), recommendation generation, approval, rejection, expiry and blocked execution. A test enumerates the
  OpenAPI POST routes and fails when a new one is not accounted for.
- **Audit content is scrubbed before it is stored** (`audit_entry`), including approval comments, which are also scrubbed
  on the approval record itself.
- **Audit API:** admin only; filter by action, entity type, entity id; page backwards with `before`.
- No schema change. Agent/assistant run traces are already stored per run (`assistant_runs`); they are linked to the
  audit trail by run id rather than by adding a request-id column.

## Consequences
- Redaction is pattern-based: a novel credential format, a person's name or an address is not caught. It reduces, not
  eliminates, accidental disclosure; secrets should still never be put in free text.
- Verified 2026-11-12 on PostgreSQL 18.6: `SqlAuditStore` filters, paging, isolation and immutability (`tests/integration/test_audit_store.py` and the 33-check end-to-end run). Not yet run on PostgreSQL 16.
- Not done (M8): metrics, tracing export, log shipping, alerting, retention and backup of the audit table, and the
  failed-authentication log/rate limit. Reads (GETs) are not audited.
- The access log records the role claimed through the membership lookup, not the JWT subject, to keep personal
  identifiers out of routine logs; the subject appears only in audit entries (as `actor`).
