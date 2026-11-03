// Shapes of the AdPilot analytics API (docs/API_DESIGN.md). Only fields the UI reads are typed.
// Money is integer paise. Ratios are numbers; undefined ratios are null, never 0.

export type Num = number | null;

export interface Meta {
  data_sources: string[];
  synthetic: boolean;
  notice: string | null;
  as_of: string | null;
  latest_date: string | null;
  mature_through: string | null;
}

export interface Envelope<T> {
  meta: Meta;
  data: T;
}

export interface Totals {
  impressions: number;
  clicks: number;
  spend_paise: number;
  sales_paise: number;
  orders: number;
  units: number;
}

export interface Kpis {
  acos_pct: Num;
  roas: Num;
  ctr_pct: Num;
  cpc_paise: Num;
  cvr_pct: Num;
  aov_paise: Num;
}

export interface Change {
  current: Num;
  previous: Num;
  absolute: Num;
  relative_pct: Num;
}

export interface DateSpan {
  start: string;
  end: string;
  days: number;
}

export interface Driver {
  name: "cpc" | "cvr" | "aov";
  contribution_points: number;
}

export interface Comparison {
  current: DateSpan;
  previous: DateSpan;
  truncated_days: number;
  changes: Record<string, Change>;
  acos_explanation: { change_points: number; main_driver: Driver; note: string } | null;
}

export interface Summary {
  period: DateSpan;
  totals: Totals;
  kpis: Kpis;
  immature_days: number;
  comparison: Comparison | null;
}

export interface TrendPoint {
  date: string;
  is_mature: boolean;
  totals: Totals;
  kpis: Kpis;
}

export interface Trends {
  period: DateSpan;
  points: TrendPoint[];
}

export type HealthStatus = "healthy" | "watch" | "unhealthy" | "insufficient_data";

export interface CampaignRow {
  info: { id: string; external_id: string; name: string; targeting_type: string; daily_budget_paise: number };
  totals: Totals;
  kpis: Kpis;
  spend_share_pct: Num;
  health: { status: HealthStatus; reasons: string[] };
  budget: { utilization_pct: Num; days_at_cap_pct: Num; constrained: boolean };
}

export interface Campaigns {
  period: DateSpan;
  truncated_days: number;
  target_acos_pct: number;
  rows: CampaignRow[];
}

export interface KeywordRow {
  target: { id: string; text: string; match_type: string };
  campaign_name: string;
  totals: Totals;
  kpis: Kpis;
  low_data: boolean;
}

export interface Keywords {
  period: DateSpan;
  total_rows: number;
  rows: KeywordRow[];
}

export interface Health {
  status: string;
  ads_provider: string;
  data_source: "MOCK" | "AMAZON_ADS_API";
  execution_enabled: boolean;
}

// ---- assistant ------------------------------------------------------------------------------
export type RunStatus =
  | "answered"
  | "partial"
  | "unverified"
  | "step_limit"
  | "budget_exceeded"
  | "llm_unavailable"
  | "llm_error"
  | "refused"
  | "not_configured";

export interface DataUsed {
  evidence_id: string;
  tool: string;
  arguments: Record<string, unknown>;
  data_sources: string[];
  synthetic: boolean;
  as_of: string | null;
  period: [string, string] | null;
  truncated: boolean;
  flags: string[];
}

export interface AssistantAnswer {
  text: string;
  claims: { text: string; evidence_ids: string[] }[];
  caveats: string[];
  confidence: "high" | "medium" | "low";
  system_notes: string[];
  dropped_claims: number;
}

export interface AssistantRun {
  run_id: string;
  thread_id: string;
  status: RunStatus;
  question: string;
  answer: AssistantAnswer;
  data_used: DataUsed[];
  flags: string[];
  model: string;
  usage: { cost_usd: string };
}
