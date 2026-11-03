import { useMemo, useState } from "react";
import { analytics, ApiError } from "./api";
import { LineChart } from "./charts/LineChart";
import { CampaignTable, KeywordTable } from "./components/Tables";
import { KpiTiles } from "./components/KpiTiles";
import { isoDaysBefore, longDate, percent, rupees, rupeesCompact } from "./format";
import type { Meta } from "./types";
import { useResource } from "./useResource";

const PRESETS = [7, 28, 90] as const;
const DEFAULT_DAYS = 28;

function describe(error: Error): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "The organization id was not accepted. Check it and try again.";
    if (error.status === 404) return "No advertising data for this organization yet. Load demo data with: python -m app.cli seed-mock";
    if (error.status === 503) return "The database is not available.";
  }
  return error.message;
}

function Banner({ meta }: { meta: Meta | null }) {
  return (
    <div className="banner" role="note">
      <strong>{meta?.synthetic === false ? "Live data" : "MOCK data"}</strong>
      <span>
        {meta?.notice ?? (meta?.synthetic === false ? "Data from the Amazon Ads API." : "Synthetic data, not live Amazon data.")}
        {meta?.as_of ? ` Loaded ${new Date(meta.as_of).toLocaleString("en-GB")}.` : ""}
      </span>
    </div>
  );
}

export function Dashboard({ orgId, onChangeOrg }: { orgId: string; onChangeOrg: () => void }) {
  const [days, setDays] = useState<number>(DEFAULT_DAYS);
  const [latest, setLatest] = useState<string | null>(null);
  const range = useMemo(
    () =>
      // The API's default window is the last 28 days, so only other presets need explicit dates.
      latest && days !== DEFAULT_DAYS
        ? { from: isoDaysBefore(latest, days - 1), to: latest }
        : { from: undefined, to: undefined },
    [latest, days],
  );

  const deps = [orgId, range.from, range.to];
  const summary = useResource((s) => analytics.summary(orgId, range.from, range.to, s), deps);
  const trends = useResource((s) => analytics.trends(orgId, range.from, range.to, s), deps);
  const campaigns = useResource((s) => analytics.campaigns(orgId, range.from, range.to, s), deps);
  const keywords = useResource((s) => analytics.keywords(orgId, range.from, range.to, s), deps);

  const learned = summary.data?.meta.latest_date ?? null;
  if (learned && learned !== latest) setLatest(learned); // learn the latest data date from the first response

  const error = summary.error ?? trends.error ?? campaigns.error ?? keywords.error;
  const loading = summary.loading || trends.loading || campaigns.loading || keywords.loading;
  const meta = summary.data?.meta ?? null;
  const s = summary.data?.data ?? null;

  const points = trends.data?.data.points ?? [];
  const dates = points.map((p) => p.date);
  const lastMature = points.reduce((acc, p, i) => (p.is_mature ? i : acc), -1);
  const target = campaigns.data?.data.target_acos_pct ?? 30;

  return (
    <div>
      <Banner meta={meta} />

      <div className="filters">
        <div role="group" aria-label="Date range" className="presets">
          {PRESETS.map((p) => (
            <button key={p} type="button" aria-pressed={days === p} disabled={latest === null} onClick={() => setDays(p)}>
              Last {p} days
            </button>
          ))}
        </div>
        <div className="tenant">
          <span className="muted">Organization (dev)</span> <code>{orgId.slice(0, 8)}…</code>
          <button type="button" className="link" onClick={onChangeOrg}>
            Change
          </button>
        </div>
      </div>

      {error && (
        <p role="alert" className="error">
          {describe(error)}
        </p>
      )}

      <main className={loading && s ? "refreshing" : undefined} aria-busy={loading}>
        {!s && !error && <p className="muted">Loading…</p>}

        {s && (
          <>
            <p className="muted period">
              {longDate(s.period.start)} – {longDate(s.period.end)}
              {meta?.mature_through ? ` · complete through ${longDate(meta.mature_through)}` : ""}
              {s.comparison
                ? ` · changes compare ${longDate(s.comparison.current.start)} – ${longDate(s.comparison.current.end)} with the ${s.comparison.previous.days} days before it`
                : ""}
            </p>
            {s.immature_days > 0 && (
              <p className="notice" role="note">
                <span aria-hidden="true">⚠</span> The latest {s.immature_days} days are still inside Amazon&apos;s attribution window. Sales
                are under-reported there, so recent ACOS looks worse than it will. They are shown dashed and left out of every comparison
                and judgement.
              </p>
            )}
            <KpiTiles summary={s} comparison={s.comparison} />
          </>
        )}

        {points.length > 0 && (
          <div className="charts">
            <LineChart
              title="Ad spend and ad sales"
              subtitle="Per day, in rupees. Dashed days are still maturing."
              dates={dates}
              matureThrough={lastMature}
              axisFormat={rupeesCompact}
              series={[
                { key: "spend", label: "Ad spend", color: "var(--series-1)", values: points.map((p) => p.totals.spend_paise), format: rupees },
                { key: "sales", label: "Ad sales", color: "var(--series-2)", values: points.map((p) => p.totals.sales_paise), format: rupees },
              ]}
            />
            <LineChart
              title="ACOS"
              subtitle="Spend as a share of ad sales, per day. Lower is better."
              dates={dates}
              matureThrough={lastMature}
              axisFormat={(v) => `${v}%`}
              reference={{ value: target, label: `Target ${target}%` }}
              series={[
                { key: "acos", label: "ACOS", color: "var(--series-3)", values: points.map((p) => p.kpis.acos_pct), format: (v) => percent(v) },
              ]}
            />
          </div>
        )}

        {campaigns.data && (
          <CampaignTable
            rows={campaigns.data.data.rows}
            targetAcos={campaigns.data.data.target_acos_pct}
            truncatedDays={campaigns.data.data.truncated_days}
          />
        )}
        {keywords.data && <KeywordTable rows={keywords.data.data.rows} total={keywords.data.data.total_rows} />}
      </main>
    </div>
  );
}
