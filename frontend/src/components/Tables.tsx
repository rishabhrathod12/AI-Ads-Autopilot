import { count, percent, rupees } from "../format";
import type { CampaignRow, HealthStatus, KeywordRow } from "../types";

// Status always ships an icon AND a label: colour never carries meaning alone.
const HEALTH: Record<HealthStatus, { icon: string; label: string; tone: string }> = {
  healthy: { icon: "✓", label: "Healthy", tone: "good" },
  watch: { icon: "!", label: "Watch", tone: "warn" },
  unhealthy: { icon: "▲", label: "Unhealthy", tone: "bad" },
  insufficient_data: { icon: "○", label: "Not enough data", tone: "neutral" },
};

export function HealthBadge({ status, reasons }: { status: HealthStatus; reasons: string[] }) {
  const h = HEALTH[status];
  return (
    <span className={`badge ${h.tone}`} title={reasons.join(" ")}>
      <span aria-hidden="true">{h.icon}</span> {h.label}
    </span>
  );
}

export function CampaignTable({ rows, targetAcos, truncatedDays }: { rows: CampaignRow[]; targetAcos: number; truncatedDays: number }) {
  const sorted = [...rows].sort((a, b) => b.totals.spend_paise - a.totals.spend_paise);
  return (
    <section aria-labelledby="campaigns-h">
      <h2 id="campaigns-h">Campaigns</h2>
      <p className="muted">
        Judged on mature days only{truncatedDays > 0 ? ` (latest ${truncatedDays} days excluded)` : ""}. Health compares ACOS with a{" "}
        {targetAcos}% target.
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Campaign</th>
              <th scope="col">Health</th>
              <th scope="col" className="num">Spend</th>
              <th scope="col" className="num">Share</th>
              <th scope="col" className="num">Ad sales</th>
              <th scope="col" className="num">ACOS</th>
              <th scope="col" className="num">Conv. rate</th>
              <th scope="col">Budget</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.info.id}>
                <th scope="row">{r.info.name}</th>
                <td>
                  <HealthBadge status={r.health.status} reasons={r.health.reasons} />
                </td>
                <td className="num">{rupees(r.totals.spend_paise)}</td>
                <td className="num">{percent(r.spend_share_pct)}</td>
                <td className="num">{rupees(r.totals.sales_paise)}</td>
                <td className="num">{percent(r.kpis.acos_pct)}</td>
                <td className="num">{percent(r.kpis.cvr_pct)}</td>
                <td>
                  {r.budget.constrained ? (
                    <span className="badge warn" title="Spend reached the daily budget on at least half of the days.">
                      <span aria-hidden="true">⏸</span> Budget-capped
                    </span>
                  ) : (
                    <span className="muted">{percent(r.budget.utilization_pct, 0)} used</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function KeywordTable({ rows, total }: { rows: KeywordRow[]; total: number }) {
  return (
    <section aria-labelledby="keywords-h">
      <h2 id="keywords-h">Top keywords by spend</h2>
      <p className="muted">
        Showing {rows.length} of {count(total)} targets with traffic. Ratios on few clicks are unreliable and are marked.
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Keyword</th>
              <th scope="col">Match</th>
              <th scope="col">Campaign</th>
              <th scope="col" className="num">Spend</th>
              <th scope="col" className="num">Clicks</th>
              <th scope="col" className="num">Orders</th>
              <th scope="col" className="num">ACOS</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.target.id}>
                <th scope="row">
                  {r.target.text}
                  {r.low_data && <span className="badge neutral" title="Fewer than 10 clicks: too little data to judge."> low data</span>}
                </th>
                <td>{r.target.match_type}</td>
                <td className="muted">{r.campaign_name}</td>
                <td className="num">{rupees(r.totals.spend_paise)}</td>
                <td className="num">{count(r.totals.clicks)}</td>
                <td className="num">{count(r.totals.orders)}</td>
                <td className="num">{percent(r.kpis.acos_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
