import { vi } from "vitest";
import type { Campaigns, Envelope, Health, Keywords, Meta, Summary, Trends } from "./types";

export const ORG = "11111111-1111-4111-8111-111111111111";

export const META: Meta = {
  data_sources: ["MOCK"],
  synthetic: true,
  notice: "MOCK data: synthetic, not live Amazon data.",
  as_of: "2026-11-01T06:00:00+00:00",
  latest_date: "2026-10-31",
  mature_through: "2026-10-28",
};

const totals = (spend: number, sales: number) => ({
  impressions: 100000,
  clicks: 500,
  spend_paise: spend,
  sales_paise: sales,
  orders: 40,
  units: 42,
});
const kpis = (acos: number | null) => ({
  acos_pct: acos,
  roas: acos ? 100 / acos : null,
  ctr_pct: 0.5,
  cpc_paise: 1300,
  cvr_pct: 8,
  aov_paise: 90000,
});

export const SUMMARY: Summary = {
  period: { start: "2026-10-04", end: "2026-10-31", days: 28 },
  totals: totals(12345600, 52800000),
  kpis: kpis(23.4),
  immature_days: 3,
  comparison: {
    current: { start: "2026-10-04", end: "2026-10-28", days: 25 },
    previous: { start: "2026-09-09", end: "2026-10-03", days: 25 },
    truncated_days: 3,
    changes: {
      spend_paise: { current: 1, previous: 1, absolute: 0, relative_pct: 12.5 },
      sales_paise: { current: 1, previous: 1, absolute: 0, relative_pct: -4.2 },
      acos_pct: { current: 23.4, previous: 20.9, absolute: 2.5, relative_pct: 12 },
      cvr_pct: { current: 8, previous: 8, absolute: 0, relative_pct: 0 },
    },
    acos_explanation: { change_points: 2.5, main_driver: { name: "cvr", contribution_points: 2.1 }, note: "" },
  },
};

export const TRENDS: Trends = {
  period: SUMMARY.period,
  points: ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31"].map((date, i) => ({
    date,
    is_mature: i <= 2,
    totals: totals(400000 + i * 10000, 1700000 + i * 20000),
    kpis: kpis(i === 4 ? null : 23 + i),
  })),
};

export const CAMPAIGNS: Campaigns = {
  period: { start: "2026-10-04", end: "2026-10-28", days: 25 },
  truncated_days: 3,
  target_acos_pct: 30,
  rows: [
    {
      info: { id: "c1", external_id: "MOCK-CMP-001", name: "SP | Auto | yoga mat", targeting_type: "auto", daily_budget_paise: 100000 },
      totals: totals(2000000, 15000000),
      kpis: kpis(13.3),
      spend_share_pct: 16.2,
      health: { status: "healthy", reasons: ["ACOS 13.3% is within target 30%"] },
      budget: { utilization_pct: 61, days_at_cap_pct: 4, constrained: false },
    },
    {
      info: { id: "c2", external_id: "MOCK-CMP-002", name: "SP | Manual | steel water bottle", targeting_type: "manual", daily_budget_paise: 160000 },
      totals: totals(8000000, 16000000),
      kpis: kpis(50),
      spend_share_pct: 64.8,
      health: { status: "unhealthy", reasons: ["ACOS 50.0% is more than 1.3x target 30%"] },
      budget: { utilization_pct: 99, days_at_cap_pct: 80, constrained: true },
    },
  ],
};

export const KEYWORDS: Keywords = {
  period: CAMPAIGNS.period,
  total_rows: 1674,
  rows: [
    {
      target: { id: "t1", text: "wireless earbuds premium", match_type: "broad" },
      campaign_name: "SP | Manual | wireless earbuds",
      totals: totals(500000, 0),
      kpis: kpis(null),
      low_data: false,
    },
    {
      target: { id: "t2", text: "yoga mat 6mm", match_type: "exact" },
      campaign_name: "SP | Auto | yoga mat",
      totals: { ...totals(9000, 100000), clicks: 6, orders: 1 },
      kpis: kpis(9),
      low_data: true,
    },
  ],
};

export const HEALTH: Health = { status: "ok", ads_provider: "mock", data_source: "MOCK", execution_enabled: false };

export function envelope<T>(data: T, meta: Meta = META): Envelope<T> {
  return { meta, data };
}

const FAIL = Symbol("fail");
interface Failure {
  [FAIL]: true;
  status: number;
  body: unknown;
}
/** An error response, e.g. failWith(404, { title: "No advertising data" }). */
export function failWith(status: number, body: unknown = {}): Failure {
  return { [FAIL]: true, status, body };
}

type Handler = unknown;
export interface Calls {
  urls: string[];
  headers: Record<string, string>[];
}

/** Routes by path. A handler `{status, body}` simulates an error response. */
export function stubApi(overrides: Record<string, Handler> = {}): Calls {
  const routes: Record<string, Handler> = {
    "/health": HEALTH,
    "/dashboard/summary": envelope(SUMMARY),
    "/dashboard/trends": envelope(TRENDS),
    "/campaigns": envelope(CAMPAIGNS),
    "/keywords": envelope(KEYWORDS),
    ...overrides,
  };
  const calls: Calls = { urls: [], headers: [] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      calls.urls.push(String(input));
      calls.headers.push((init?.headers ?? {}) as Record<string, string>);
      const path = String(input).split("?")[0] ?? "";
      const key = Object.keys(routes).find((k) => path.endsWith(k));
      const handler = key ? routes[key] : undefined;
      if (handler && typeof handler === "object" && FAIL in handler) {
        const h = handler as Failure;
        return { ok: false, status: h.status, json: async () => h.body };
      }
      return { ok: true, status: 200, json: async () => handler };
    }),
  );
  return calls;
}
