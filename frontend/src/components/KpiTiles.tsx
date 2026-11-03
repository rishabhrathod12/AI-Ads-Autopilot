import { count, DASH, multiple, percent, rupees, rupeesExact } from "../format";
import type { Change, Comparison, Num, Summary } from "../types";

interface TileSpec {
  label: string;
  hint?: string;
  value: (s: Summary) => string;
  /** Key into comparison.changes. */
  changeKey: string;
  /** Percentage metrics report a change in points; the rest a relative change. */
  deltaStyle: "points" | "relative";
  /** Which direction is an improvement. null = neither (e.g. spend). */
  good: "up" | "down" | null;
}

const TILES: TileSpec[] = [
  { label: "Ad spend", value: (s) => rupees(s.totals.spend_paise), changeKey: "spend_paise", deltaStyle: "relative", good: null },
  {
    label: "Ad sales",
    hint: "Advertising-attributed sales, not total business revenue.",
    value: (s) => rupees(s.totals.sales_paise),
    changeKey: "sales_paise",
    deltaStyle: "relative",
    good: "up",
  },
  { label: "ACOS", value: (s) => percent(s.kpis.acos_pct), changeKey: "acos_pct", deltaStyle: "points", good: "down" },
  { label: "ROAS", value: (s) => multiple(s.kpis.roas), changeKey: "roas", deltaStyle: "relative", good: "up" },
  { label: "Orders", value: (s) => count(s.totals.orders), changeKey: "orders", deltaStyle: "relative", good: "up" },
  { label: "CTR", value: (s) => percent(s.kpis.ctr_pct, 2), changeKey: "ctr_pct", deltaStyle: "points", good: "up" },
  { label: "Conversion rate", value: (s) => percent(s.kpis.cvr_pct), changeKey: "cvr_pct", deltaStyle: "points", good: "up" },
  { label: "CPC", value: (s) => rupeesExact(s.kpis.cpc_paise), changeKey: "cpc_paise", deltaStyle: "relative", good: "down" },
];

interface Delta {
  text: string;
  tone: "good" | "bad" | "neutral";
  arrow: "▲" | "▼" | "■";
}

export function describeDelta(change: Change | undefined, style: "points" | "relative", good: "up" | "down" | null): Delta | null {
  if (!change) return null;
  const amount: Num = style === "points" ? change.absolute : change.relative_pct;
  if (amount === null) return null;
  const rounded = Number(amount.toFixed(1));
  if (rounded === 0) return { text: style === "points" ? "0.0 pts" : "0.0%", tone: "neutral", arrow: "■" };
  const up = rounded > 0;
  const sign = up ? "+" : "−";
  const text = `${sign}${Math.abs(rounded).toFixed(1)}${style === "points" ? " pts" : "%"}`;
  const tone = good === null ? "neutral" : (good === "up") === up ? "good" : "bad";
  return { text, tone, arrow: up ? "▲" : "▼" };
}

export function KpiTiles({ summary, comparison }: { summary: Summary; comparison: Comparison | null }) {
  return (
    <section aria-label="Key metrics" className="tiles">
      {TILES.map((t) => {
        const delta = describeDelta(comparison?.changes[t.changeKey], t.deltaStyle, t.good);
        return (
          <div className="tile" key={t.label} title={t.hint}>
            <div className="tile-label">{t.label}</div>
            <div className="tile-value">{t.value(summary)}</div>
            <div className={`tile-delta ${delta?.tone ?? "neutral"}`}>
              {delta ? (
                <>
                  <span aria-hidden="true">{delta.arrow}</span> {delta.text}
                  <span className="visually-hidden">
                    {delta.tone === "good" ? " (better)" : delta.tone === "bad" ? " (worse)" : ""}
                  </span>
                </>
              ) : (
                <span className="muted">{DASH}</span>
              )}
            </div>
          </div>
        );
      })}
    </section>
  );
}
