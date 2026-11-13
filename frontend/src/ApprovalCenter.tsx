import { useState } from "react";
import { ApiError, recommendations } from "./api";
import { rupees } from "./format";
import { ROLES, type RecStatus, type RecType, type Recommendation, type Role } from "./types";
import { useResource } from "./useResource";

// The role menu is development only (ADR 0014/0021): when signed in, the role comes from the membership.
const FILTERS: (RecStatus | "all")[] = ["pending", "approved", "rejected", "expired", "all"];

const TYPE_LABEL: Record<RecType, string> = {
  bid_change: "Bid change",
  budget_change: "Daily budget change",
  negative_keyword: "Negative keyword",
};

const canGenerate = (role: Role) => role !== "viewer";
const canDecide = (role: Role) => role === "admin" || role === "owner";

/** A pending recommendation past its expiry can no longer be decided; the server enforces it too. */
function effectiveStatus(r: Recommendation, now: number): RecStatus {
  return r.status === "pending" && Date.parse(r.expires_at) <= now ? "expired" : r.status;
}

function change(r: Recommendation): string {
  if (r.type === "negative_keyword") return `Add “${r.proposed_text ?? ""}” as a negative keyword`;
  return `${rupees(r.current_value_paise)} → ${rupees(r.proposed_value_paise)}`;
}

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return "Your role does not allow this action.";
    if (error.status === 409) return `${error.message} Reload the list and review the current version.`;
    if (error.status === 404) return "That recommendation no longer exists.";
  }
  return error instanceof Error ? error.message : "Something went wrong.";
}

function Evidence({ evidence }: { evidence: Record<string, unknown> }) {
  const rows = Object.entries(evidence);
  return (
    <details className="evidence">
      <summary>Evidence behind this proposal</summary>
      <dl>
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt>{k.replace(/_/g, " ")}</dt>
            <dd>{typeof v === "string" || typeof v === "number" ? String(v) : JSON.stringify(v)}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

function Card({
  rec,
  now,
  role,
  busy,
  onDecide,
}: {
  rec: Recommendation;
  now: number;
  role: Role;
  busy: boolean;
  onDecide: (verb: "approve" | "reject", comment: string) => void;
}) {
  const status = effectiveStatus(rec, now);
  const [pending, setPending] = useState<"approve" | "reject" | null>(null);
  const [comment, setComment] = useState("");
  const decidable = status === "pending" && canDecide(role);
  return (
    <li className={`reco reco-${status}`}>
      <div className="reco-head">
        <strong>{TYPE_LABEL[rec.type]}</strong>
        <span className="reco-entity">{rec.entity.label}</span>
        <span className={`reco-status reco-status-${status}`}>{status}</span>
        {rec.synthetic && <strong className="chip-tag">MOCK</strong>}
      </div>
      <p className="reco-change">{change(rec)}</p>
      <p>{rec.rationale}</p>
      <p className="muted">
        Confidence: {rec.confidence}. {rec.expected_impact}
      </p>
      <p className="muted">
        Expires {new Date(rec.expires_at).toLocaleString("en-GB")}. Content hash <code>{rec.content_hash.slice(0, 12)}</code>
      </p>
      <Evidence evidence={rec.evidence} />
      {rec.approval && (
        <p className="muted">
          {rec.approval.decision === "approve" ? "Approved" : "Rejected"} by {rec.approval.decided_by} on{" "}
          {new Date(rec.approval.decided_at).toLocaleString("en-GB")}
          {rec.approval.comment ? `: “${rec.approval.comment}”` : ""}. Nothing was sent to Amazon.
        </p>
      )}
      {decidable && pending === null && (
        <div className="reco-actions">
          <button type="button" disabled={busy} onClick={() => setPending("approve")} aria-label={`Approve ${rec.entity.label}`}>
            Approve
          </button>
          <button type="button" className="secondary" disabled={busy} onClick={() => setPending("reject")} aria-label={`Reject ${rec.entity.label}`}>
            Reject
          </button>
        </div>
      )}
      {decidable && pending !== null && (
        <div className="reco-confirm" role="group" aria-label={`Confirm ${pending}`}>
          <p>
            {pending === "approve" ? "Approve" : "Reject"} <strong>{change(rec)}</strong> for {rec.entity.label}? This records your
            decision only; <strong>nothing is executed</strong>.
          </p>
          <label>
            Comment (optional)
            <input value={comment} maxLength={1000} onChange={(e) => setComment(e.target.value)} />
          </label>
          <div className="reco-actions">
            <button type="button" disabled={busy} onClick={() => onDecide(pending, comment)}>
              Confirm {pending}
            </button>
            <button type="button" className="secondary" disabled={busy} onClick={() => setPending(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

export function ApprovalCenter({ orgId, fixedRole }: { orgId: string; fixedRole?: Role }) {
  const [devRole, setRole] = useState<Role>("analyst");
  const role = fixedRole ?? devRole;
  const [filter, setFilter] = useState<RecStatus | "all">("pending");
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  // Expiry is judged as of the moment the list was loaded (the server enforces it regardless).
  const list = useResource(
    async (s) => ({ ...(await recommendations.list(orgId, role, filter, s)), loadedAt: Date.now() }),
    [orgId, role, filter, version],
  );
  const now = list.data?.loadedAt ?? 0;

  async function run(action: () => Promise<string>) {
    setBusy(true);
    setNotice(null);
    try {
      setNotice({ kind: "ok", text: await action() });
    } catch (e) {
      setNotice({ kind: "error", text: describe(e) });
    } finally {
      setBusy(false);
      setVersion((v) => v + 1);
    }
  }

  const items = list.data?.data ?? [];
  const meta = list.data?.meta;
  return (
    <section className="approval" aria-labelledby="approval-title">
      <div className="approval-head">
        <h2 id="approval-title">Approval Center</h2>
        {fixedRole ? (
          <p className="muted role-note">Your role: {fixedRole}</p>
        ) : (
          <label className="role-picker">
            Acting as (development only)
            <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <p className="muted">
        Proposals are computed from your analytics by fixed rules. Approving records a decision bound to the content you reviewed;
        <strong> nothing is sent to Amazon and ad execution is disabled.</strong>
      </p>
      {meta?.synthetic && (
        <div className="banner" role="note">
          <strong>MOCK data</strong>
          <span>{meta.notice}</span>
        </div>
      )}
      <div className="reco-toolbar">
        <div role="group" aria-label="Filter by status">
          {FILTERS.map((f) => (
            <button key={f} type="button" aria-pressed={f === filter} onClick={() => setFilter(f)}>
              {f}
            </button>
          ))}
        </div>
        {canGenerate(role) && (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              run(async () => {
                const r = (await recommendations.generate(orgId, role)).data;
                return `${r.created} new proposal(s); ${r.already_stored} already stored.`;
              })
            }
          >
            Generate proposals
          </button>
        )}
      </div>
      {notice && (
        <p role={notice.kind === "error" ? "alert" : "status"} className={notice.kind === "error" ? "error" : "muted"}>
          {notice.text}
        </p>
      )}
      {list.error && <p role="alert" className="error">{describe(list.error)}</p>}
      {!list.error && !list.loading && items.length === 0 && <p className="muted">No {filter === "all" ? "" : filter} recommendations.</p>}
      <ul className={list.loading ? "reco-list loading" : "reco-list"}>
        {items.map((r) => (
          <Card
            key={`${r.id}:${r.content_hash}`}
            rec={r}
            now={now}
            role={role}
            busy={busy}
            onDecide={(verb, comment) =>
              run(async () => {
                await recommendations.decide(orgId, role, r.id, verb, r.content_hash, comment);
                return `${verb === "approve" ? "Approved" : "Rejected"}. Nothing was executed.`;
              })
            }
          />
        ))}
      </ul>
    </section>
  );
}
