import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Insights } from "./Insights";
import { failWith, ORG, stubApi } from "./test-fixtures";

afterEach(() => vi.unstubAllGlobals());

const META = { data_sources: ["MOCK"], synthetic: true, notice: "MOCK data: synthetic, not live Amazon data.", as_of: null, latest_date: "2026-10-31", mature_through: "2026-10-28" };
const TOTALS = { impressions: 5000, clicks: 80, spend_paise: 123400, sales_paise: 0, orders: 0, units: 0 };
const KPIS = { acos_pct: 41.5, roas: 2.4, ctr_pct: 1, cpc_paise: 900, cvr_pct: 4, aov_paise: 50000 };
const REF = { cvr: 0.08, cvr_pct: 8, scope: "campaign" };

const FINDINGS = {
  meta: META,
  data: {
    period: { start: "2026-10-04", end: "2026-10-31", days: 28 },
    truncated_days: 3,
    target_acos_pct: 30,
    wasted_targets: [
      { target: { id: "t1", external_id: "T1", text: "cheap yoga mat", match_type: "broad" }, campaign_name: "Yoga SP", totals: TOTALS, level: "confirmed", chance_of_zero_orders_pct: 1.8, reference: REF, projected_monthly_spend_paise: 400000 },
    ],
    underperforming_targets: [
      { target: { id: "t2", external_id: "T2", text: "foam roller", match_type: "exact" }, campaign_name: "Fitness SP", totals: { ...TOTALS, orders: 1, sales_paise: 90000 }, kpis: KPIS, chance_pct: 3.2, reference: REF },
    ],
    negative_candidates: [
      { term: { id: "s1", external_id: "S1", term: "free yoga videos" }, parent_target_text: "yoga", campaign_name: "Yoga SP", totals: TOTALS, level: "watch", chance_of_zero_orders_pct: 12, reference: REF, projected_monthly_spend_paise: 100000 },
    ],
    opportunities: [],
    low_data_targets: 9,
    low_data_terms: 4,
    unjudged_campaigns: 1,
    counts: { wasted_targets: 7, underperforming_targets: 1, negative_candidates: 1, opportunities: 0, limit_per_list: 25 },
  },
};

const EPISODE = {
  campaign: { id: "c1", name: "Mat Campaign" },
  metric: "cvr",
  direction: "down",
  severity: "alert",
  status: "ongoing",
  onset: "2026-10-20",
  detected_on: "2026-10-24",
  alert_on: "2026-10-27",
  last_day: "2026-10-28",
  reference_value: 0.08,
  observed_value: 0.04,
  relative_change_pct: -50,
};
const ANOMALIES = {
  meta: META,
  data: {
    monitor: { start: "2026-10-15", end: "2026-10-28", days: 14 },
    reference: { start: "2026-08-20", end: "2026-10-14", days: 56 },
    episodes: [EPISODE],
    skipped: [{ campaign_external_id: "C9", metric: "cpc", reason: "too little history" }],
    acos_context: { c1: { previous_pct: 20, current_pct: 31.5, change_points: 11.5, main_driver: { name: "cvr", contribution_points: 10.2 } } },
  },
};

const points = (base: number) =>
  Array.from({ length: 14 }, (_, i) => ({ date: `2026-11-${String(i + 1).padStart(2, "0")}`, point: base + i * 1000, lower: base - 5000, upper: base + 9000 }));
const stored = (metric: "spend" | "sales") => ({
  id: `f-${metric}`,
  metric,
  model: "seasonal_naive",
  used_baseline: true,
  train_days: 117,
  train_end: "2026-10-28",
  horizon_start: "2026-11-01",
  horizon_end: "2026-11-14",
  points: points(metric === "spend" ? 400000 : 1700000),
  params: { reason: "default forecast: seasonal-naive", interval_level: 0.8, cv_coverage: 0.78 },
  data_source: "MOCK",
  created_at: "2026-11-01T06:00:00Z",
});
const FORECAST = { meta: META, data: { spend: stored("spend"), sales: stored("sales"), acos_pct: 23.4, acos_note: "Derived from the stored spend and sales forecasts." } };

describe("Insights", () => {
  it("shows keyword findings with levels, totals and the honest caveats", async () => {
    stubApi({ "/keywords/findings": FINDINGS });
    render(<Insights orgId={ORG} role="analyst" />);
    expect(await screen.findByText("cheap yoga mat")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Wasted spend on keywords (7)" })).toBeInTheDocument(); // total, not the shown 1
    expect(screen.getByText("Confirmed")).toBeInTheDocument();
    expect(screen.getByText("Watch")).toBeInTheDocument();
    expect(screen.getByText("None found.")).toBeInTheDocument(); // opportunities
    expect(screen.getByText(/weak converter/)).toBeInTheDocument();
    expect(screen.getByText(/9 targets and 4 search terms/)).toBeInTheDocument();
    expect(screen.getByText(/Each list shows up to 25 items/)).toBeInTheDocument();
    expect(screen.getByText("MOCK data.")).toBeInTheDocument();
  });

  it("shows anomalies in the right units and the ACOS driver", async () => {
    stubApi({ "/keywords/findings": FINDINGS, "/anomalies": ANOMALIES });
    render(<Insights orgId={ORG} role="analyst" />);
    fireEvent.click(screen.getByRole("button", { name: "Anomalies" }));
    const row = (await screen.findByRole("rowheader", { name: "Mat Campaign" })).closest("tr")!;
    expect(within(row).getByText(/Conversion rate/)).toBeInTheDocument();
    expect(within(row).getByText("8.0%")).toBeInTheDocument(); // a fraction 0.08 is shown as a percentage
    expect(within(row).getByText("4.0%")).toBeInTheDocument();
    expect(within(row).getByText("Alert")).toBeInTheDocument();
    expect(within(row).getByText("cvr (+10.2 pts)")).toBeInTheDocument();
    expect(screen.getByText(/1 campaign metric\(s\) were not monitored/)).toBeInTheDocument();
    expect(screen.getByText(/not why/)).toBeInTheDocument();
  });

  it("says so plainly when nothing has shifted", async () => {
    stubApi({ "/anomalies": { meta: META, data: { ...ANOMALIES.data, episodes: [], skipped: [], acos_context: {} } } });
    render(<Insights orgId={ORG} role="analyst" />);
    fireEvent.click(screen.getByRole("button", { name: "Anomalies" }));
    expect(await screen.findByText("No sustained shifts detected.")).toBeInTheDocument();
  });

  it("explains a missing forecast, lets an analyst create it, then shows it", async () => {
    let created = false;
    const calls = stubApi({ "/forecasts/account": failWith(404, { title: "Not Found" }) });
    const base = globalThis.fetch as unknown as (input: string, init?: RequestInit) => Promise<unknown>;
    const methods: string[] = [];
    vi.stubGlobal("fetch", async (input: string, init?: RequestInit) => {
      if (String(input).includes("/forecasts/account")) {
        methods.push(init?.method ?? "GET");
        if (init?.method === "POST") {
          created = true;
          return { ok: true, status: 201, json: async () => ({}) };
        }
        if (created) return { ok: true, status: 200, json: async () => FORECAST };
      }
      return base(input, init);
    });
    render(<Insights orgId={ORG} role="analyst" />);
    fireEvent.click(screen.getByRole("button", { name: "Forecast" }));
    expect(await screen.findByText("No forecast has been created yet.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create forecast" }));
    expect(await screen.findByText(/Expected ACOS over the period/)).toBeInTheDocument();
    expect(screen.getByText("23.4%")).toBeInTheDocument();
    expect(screen.getByText("seasonal naive")).toBeInTheDocument();
    expect(screen.getByText(/learned models were not shown to beat it/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh forecast" })).toBeInTheDocument();
    expect(methods).toEqual(["GET", "POST", "GET"]);
    expect(calls.urls.length).toBeGreaterThan(0);
  });

  it("does not offer forecast creation to a viewer", async () => {
    stubApi({ "/forecasts/account": FORECAST });
    render(<Insights orgId={ORG} role="viewer" />);
    fireEvent.click(screen.getByRole("button", { name: "Forecast" }));
    expect(await screen.findByText(/Expected ACOS over the period/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create forecast" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Refresh forecast" })).toBeNull();
    expect(screen.getByText(/Analysts and above can create a forecast/)).toBeInTheDocument();
  });

  it("shows the audit trail to admins only", async () => {
    const entries = [{ id: "a1", actor: "admin-1", action: "recommendation.approved", entity: { type: "recommendation", id: "r-9" }, request_id: null, created_at: "2026-11-01T06:00:00Z" }];
    stubApi({ "/audit-logs": { data: entries, next_before: null } });
    const { unmount } = render(<Insights orgId={ORG} role="analyst" />);
    expect(screen.queryByRole("button", { name: "Audit trail" })).toBeNull();
    unmount();
    render(<Insights orgId={ORG} role="admin" />);
    fireEvent.click(screen.getByRole("button", { name: "Audit trail" }));
    expect(await screen.findByText("recommendation.approved")).toBeInTheDocument();
    expect(screen.getByText("admin-1")).toBeInTheDocument();
    expect(screen.getByText(/cannot be edited or deleted/)).toBeInTheDocument();
  });

  it("reports a load error without breaking the page", async () => {
    stubApi({ "/keywords/findings": failWith(404, { title: "No advertising data" }) });
    render(<Insights orgId={ORG} role="analyst" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("No advertising data for this organization yet.");
    fireEvent.click(screen.getByRole("button", { name: "Anomalies" })); // other tabs still usable
    await waitFor(() => expect(screen.getByRole("group", { name: "Insight type" })).toBeInTheDocument());
  });
});
