import { useState } from "react";
import { ApiError, insights } from "./api";
import { LineChart } from "./charts/LineChart";
import { count, longDate, percent, rupees, rupeesCompact, shortDate } from "./format";
import type { AnomalyEpisode, FindingLevel, Meta, Role, StoredForecast } from "./types";
import { useResource } from "./useResource";

type Tab = "findings" | "anomalies" | "forecast" | "audit";

function describe(error: Error): string {
  if (error instanceof ApiError && error.status === 404) return "No advertising data for this organization yet.";
  return error.message;
}

function MockNote({ meta }: { meta: Meta | undefined }) {
  return meta?.synthetic ? (
    <p className="muted" role="note">
      <strong>MOCK data.</strong> {meta.notice}
    </p>
  ) : null;
}

const LEVEL: Record<FindingLevel, { icon: string; label: string; tone: string; hint: string }> = {
  confirmed: { icon: "▲", label: "Confirmed", tone: "bad", hint: "Zero orders would be 5% likely or less by luck at the campaign's own conversion rate." },
  watch: { icon: "!", label: "Watch", tone: "warn", hint: "Zero orders would be 20% likely or less by luck. Keep an eye on it." },
};

function Level({ level }: { level: FindingLevel }) {
  const l = LEVEL[level];
  return (
    <span className={`badge ${l.tone}`} title={l.hint}>
      <span aria-hidden="true">{l.icon}</span> {l.label}
    </span>
  );
}

function Findings({ orgId }: { orgId: string }) {
  const res = useResource((s) => insights.findings(orgId, s), [orgId]);
  if (res.error) return <p role="alert" className="error">{describe(res.error)}</p>;
  if (!res.data) return <p className="muted">Loading findings…</p>;
  const f = res.data.data;
  const c = f.counts;
  return (
    <div>
      <MockNote meta={res.data.meta} />
      <p className="muted">
        {longDate(f.period.start)} – {longDate(f.period.end)}, mature days only ({f.target_acos_pct}% target ACOS). These are observations to
        review, not instructions. A &quot;confirmed&quot; item is a weak converter at its campaign&apos;s average rate, not proof the term is
        irrelevant.
      </p>
      <p className="muted">
        Not enough data to judge: {count(f.low_data_targets)} targets and {count(f.low_data_terms)} search terms (under 10 clicks).
        {f.unjudged_campaigns > 0 && ` ${f.unjudged_campaigns} campaign(s) have no usable conversion-rate reference.`}
      </p>

      <h3>Wasted spend on keywords ({count(c.wasted_targets)})</h3>
      {f.wasted_targets.length === 0 ? <p className="muted">None found.</p> : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Keyword</th>
                <th scope="col">Campaign</th>
                <th scope="col" className="num">Spend</th>
                <th scope="col" className="num">Clicks</th>
                <th scope="col" className="num">Chance by luck</th>
                <th scope="col">Level</th>
                <th scope="col" className="num">Monthly at this rate</th>
              </tr>
            </thead>
            <tbody>
              {f.wasted_targets.map((w) => (
                <tr key={w.target.id}>
                  <th scope="row">{w.target.text} <span className="muted">({w.target.match_type})</span></th>
                  <td className="muted">{w.campaign_name}</td>
                  <td className="num">{rupees(w.totals.spend_paise)}</td>
                  <td className="num">{count(w.totals.clicks)}</td>
                  <td className="num">{percent(w.chance_of_zero_orders_pct)}</td>
                  <td><Level level={w.level} /></td>
                  <td className="num">{rupees(w.projected_monthly_spend_paise)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3>Underperforming keywords ({count(c.underperforming_targets)})</h3>
      {f.underperforming_targets.length === 0 ? <p className="muted">None found.</p> : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Keyword</th>
                <th scope="col">Campaign</th>
                <th scope="col" className="num">Spend</th>
                <th scope="col" className="num">Orders</th>
                <th scope="col" className="num">ACOS</th>
                <th scope="col" className="num">Chance by luck</th>
              </tr>
            </thead>
            <tbody>
              {f.underperforming_targets.map((u) => (
                <tr key={u.target.id}>
                  <th scope="row">{u.target.text}</th>
                  <td className="muted">{u.campaign_name}</td>
                  <td className="num">{rupees(u.totals.spend_paise)}</td>
                  <td className="num">{count(u.totals.orders)}</td>
                  <td className="num">{percent(u.kpis.acos_pct)}</td>
                  <td className="num">{percent(u.chance_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3>Negative-keyword candidates ({count(c.negative_candidates)})</h3>
      {f.negative_candidates.length === 0 ? <p className="muted">None found.</p> : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Search term</th>
                <th scope="col">Triggered by</th>
                <th scope="col">Campaign</th>
                <th scope="col" className="num">Spend</th>
                <th scope="col" className="num">Clicks</th>
                <th scope="col">Level</th>
              </tr>
            </thead>
            <tbody>
              {f.negative_candidates.map((n) => (
                <tr key={n.term.id}>
                  <th scope="row">{n.term.term}</th>
                  <td className="muted">{n.parent_target_text ?? "—"}</td>
                  <td className="muted">{n.campaign_name}</td>
                  <td className="num">{rupees(n.totals.spend_paise)}</td>
                  <td className="num">{count(n.totals.clicks)}</td>
                  <td><Level level={n.level} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3>Opportunities ({count(c.opportunities)})</h3>
      {f.opportunities.length === 0 ? <p className="muted">None found.</p> : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Search term</th>
                <th scope="col">Triggered by</th>
                <th scope="col" className="num">Orders</th>
                <th scope="col" className="num">ACOS</th>
                <th scope="col" className="num">Conversion rate (95% lower bound)</th>
                <th scope="col">Strength</th>
              </tr>
            </thead>
            <tbody>
              {f.opportunities.map((o) => (
                <tr key={o.term.id}>
                  <th scope="row">{o.term.term}</th>
                  <td className="muted">{o.parent_target_text ?? "—"}</td>
                  <td className="num">{count(o.totals.orders)}</td>
                  <td className="num">{percent(o.kpis.acos_pct)}</td>
                  <td className="num">{percent(o.cvr_lower_bound_pct)}</td>
                  <td>
                    <span className={`badge ${o.strength === "strong" ? "good" : "neutral"}`}>
                      <span aria-hidden="true">{o.strength === "strong" ? "✓" : "○"}</span> {o.strength === "strong" ? "Strong" : "Moderate"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted">Each list shows up to {c.limit_per_list} items; the counts in brackets are the totals.</p>
    </div>
  );
}

const METRIC_LABEL = { cvr: "Conversion rate", cpc: "Cost per click", spend: "Daily spend" } as const;

/** Conversion rate is stored as a fraction; CPC and spend as paise. */
function metricValue(e: AnomalyEpisode, v: number): string {
  return e.metric === "cvr" ? percent(v * 100) : rupees(v);
}

function Anomalies({ orgId }: { orgId: string }) {
  const res = useResource((s) => insights.anomalies(orgId, s), [orgId]);
  if (res.error) return <p role="alert" className="error">{describe(res.error)}</p>;
  if (!res.data) return <p className="muted">Loading anomalies…</p>;
  const a = res.data.data;
  return (
    <div>
      <MockNote meta={res.data.meta} />
      <p className="muted">
        Sustained shifts in the last {a.monitor.days} mature days ({longDate(a.monitor.start)} – {longDate(a.monitor.end)}) against the{" "}
        {a.reference.days} days before. These show <em>what</em> moved, not why. A single campaign has modest traffic, so a real drop
        takes about a week to confirm.
      </p>
      {a.episodes.length === 0 ? (
        <p>No sustained shifts detected.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Campaign</th>
                <th scope="col">Metric</th>
                <th scope="col" className="num">Before</th>
                <th scope="col" className="num">Now</th>
                <th scope="col" className="num">Change</th>
                <th scope="col">Severity</th>
                <th scope="col">Status</th>
                <th scope="col">Started</th>
                <th scope="col">ACOS driver</th>
              </tr>
            </thead>
            <tbody>
              {a.episodes.map((e) => {
                const ctx = a.acos_context[e.campaign.id];
                return (
                  <tr key={`${e.campaign.id}:${e.metric}:${e.direction}`}>
                    <th scope="row">{e.campaign.name}</th>
                    <td>{METRIC_LABEL[e.metric]} {e.direction === "down" ? "↓" : "↑"}</td>
                    <td className="num">{metricValue(e, e.reference_value)}</td>
                    <td className="num">{metricValue(e, e.observed_value)}</td>
                    <td className="num">{percent(e.relative_change_pct)}</td>
                    <td>
                      <span className={`badge ${e.severity === "alert" ? "bad" : "warn"}`}>
                        <span aria-hidden="true">{e.severity === "alert" ? "▲" : "!"}</span> {e.severity === "alert" ? "Alert" : "Watch"}
                      </span>
                    </td>
                    <td>{e.status === "ongoing" ? "Ongoing" : "Subsided"}</td>
                    <td>{shortDate(e.onset)}</td>
                    <td className="muted">
                      {ctx?.main_driver ? `${ctx.main_driver.name} (${ctx.main_driver.contribution_points >= 0 ? "+" : ""}${ctx.main_driver.contribution_points.toFixed(1)} pts)` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {a.skipped.length > 0 && (
        <p className="muted">{a.skipped.length} campaign metric(s) were not monitored because their history is too thin.</p>
      )}
    </div>
  );
}

function ForecastChart({ f }: { f: StoredForecast }) {
  const dates = f.points.map((p) => p.date);
  const title = f.metric === "spend" ? "Ad spend forecast" : "Ad sales forecast";
  const colour = f.metric === "spend" ? "var(--series-1)" : "var(--series-2)";
  return (
    <LineChart
      title={title}
      subtitle={`Next ${f.points.length} days, in rupees, with an ${Math.round((f.params.interval_level ?? 0.8) * 100)}% range.`}
      dates={dates}
      matureThrough={dates.length - 1}
      axisFormat={rupeesCompact}
      series={[
        { key: "point", label: "Forecast", color: colour, values: f.points.map((p) => p.point), format: rupees },
        { key: "upper", label: "Upper", color: "var(--series-3)", values: f.points.map((p) => p.upper), format: rupees },
        { key: "lower", label: "Lower", color: "var(--series-3)", values: f.points.map((p) => p.lower), format: rupees },
      ]}
    />
  );
}

function Forecast({ orgId, canCreate, role }: { orgId: string; canCreate: boolean; role: Role | undefined }) {
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const res = useResource((s) => insights.forecast(orgId, s), [orgId, version]);

  async function create() {
    setBusy(true);
    setFailure(null);
    try {
      await insights.createForecast(orgId, role);
      setVersion((v) => v + 1);
    } catch (e) {
      setFailure(e instanceof ApiError ? e.message : "Could not create the forecast.");
    } finally {
      setBusy(false);
    }
  }

  if (res.error) return <p role="alert" className="error">{describe(res.error)}</p>;
  if (!res.data && res.loading) return <p className="muted">Loading forecast…</p>;
  const f = res.data?.data;
  return (
    <div>
      {res.data && <MockNote meta={res.data.meta} />}
      {!f ? (
        <p>No forecast has been created yet.</p>
      ) : (
        <>
          <p className="muted">
            Created {new Date(f.spend.created_at).toLocaleString("en-GB")} from {f.spend.train_days} mature days ending {longDate(f.spend.train_end)}.
            Method: <strong>{f.spend.model.replace(/_/g, " ")}</strong>
            {f.spend.used_baseline ? " (a simple baseline: learned models were not shown to beat it)" : ""}. {f.spend.params.reason}
          </p>
          <p>
            Expected ACOS over the period: <strong>{f.acos_pct === null ? "—" : percent(f.acos_pct)}</strong>{" "}
            <span className="muted">{f.acos_note}</span>
          </p>
          <div className="charts">
            <ForecastChart f={f.spend} />
            <ForecastChart f={f.sales} />
          </div>
        </>
      )}
      {failure && <p role="alert" className="error">{failure}</p>}
      {canCreate ? (
        <button type="button" disabled={busy} onClick={() => void create()}>
          {f ? "Refresh forecast" : "Create forecast"}
        </button>
      ) : (
        <p className="muted">Analysts and above can create a forecast.</p>
      )}
      {busy && <p className="muted">Backtesting models… this takes a few seconds.</p>}
    </div>
  );
}

function Audit({ orgId, role }: { orgId: string; role: Role | undefined }) {
  const res = useResource((s) => insights.audit(orgId, role, s), [orgId]);
  if (res.error) return <p role="alert" className="error">{describe(res.error)}</p>;
  if (!res.data) return <p className="muted">Loading audit trail…</p>;
  const rows = res.data.data;
  return (
    <div>
      <p className="muted">The 50 most recent recorded changes. Entries cannot be edited or deleted.</p>
      {rows.length === 0 ? <p className="muted">Nothing recorded yet.</p> : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Who</th>
                <th scope="col">Action</th>
                <th scope="col">What</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id}>
                  <td>{new Date(e.created_at).toLocaleString("en-GB")}</td>
                  <td className="muted">{e.actor}</td>
                  <th scope="row">{e.action}</th>
                  <td className="muted">{[e.entity.type, e.entity.id].filter(Boolean).join(" ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Findings, anomalies, forecast and (for admins) the audit trail: read-only views of what the analytics found. */
export function Insights({ orgId, role }: { orgId: string; role?: Role }) {
  const [tab, setTab] = useState<Tab>("findings");
  const isAdmin = role === "admin" || role === "owner";
  const canCreate = role !== "viewer"; // development flow has no real role: the API's default (analyst) applies
  const tabs: { id: Tab; label: string }[] = [
    { id: "findings", label: "Keyword findings" },
    { id: "anomalies", label: "Anomalies" },
    { id: "forecast", label: "Forecast" },
    ...(isAdmin ? [{ id: "audit" as const, label: "Audit trail" }] : []),
  ];
  return (
    <section className="insights" aria-labelledby="insights-title">
      <h2 id="insights-title">Insights</h2>
      <div role="group" aria-label="Insight type" className="insight-tabs">
        {tabs.map((t) => (
          <button key={t.id} type="button" aria-pressed={tab === t.id} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === "findings" && <Findings orgId={orgId} />}
      {tab === "anomalies" && <Anomalies orgId={orgId} />}
      {tab === "forecast" && <Forecast orgId={orgId} canCreate={canCreate} role={role} />}
      {tab === "audit" && isAdmin && <Audit orgId={orgId} role={role} />}
    </section>
  );
}
