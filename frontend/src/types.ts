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

// ---- Approval Center (docs/API_DESIGN.md, ADR 0022) ----------------------------------------------
export type RecStatus = "pending" | "approved" | "rejected" | "expired";
export type RecType = "bid_change" | "budget_change" | "negative_keyword";

export interface Approval {
  id: string;
  decision: "approve" | "reject";
  decided_by: string;
  decided_at: string;
  comment: string | null;
  content_hash: string;
}

export interface Recommendation {
  id: string;
  type: RecType;
  status: RecStatus;
  entity: { type: string; id: string; label: string };
  current_value_paise: number | null;
  proposed_value_paise: number | null;
  proposed_text: string | null;
  rationale: string;
  evidence: Record<string, unknown>;
  confidence: string;
  expected_impact: string;
  data_source: string;
  synthetic: boolean;
  content_hash: string;
  created_at: string;
  expires_at: string;
  approval?: Approval | null;
}

export interface RecEnvelope<T> {
  data: T;
  meta: { synthetic: boolean; notice: string | null };
}

export interface GenerateResult {
  proposed: number;
  created: number;
  already_stored: number;
  dropped_by_guardrails: number;
}

export const ROLES = ["viewer", "analyst", "admin", "owner"] as const;
export type Role = (typeof ROLES)[number];

export interface OrgAccess {
  id: string;
  name: string;
  role: Role;
}

export interface Me {
  subject: string;
  email: string | null;
  organizations: OrgAccess[];
}

export interface Member {
  id: string;
  email: string;
  role: Role;
  status: "active" | "invited";
  created_at: string;
}

// ---- Insights: keyword findings, anomalies, forecast, audit trail ---------------------------------

export interface Span {
  start: string;
  end: string;
  days: number;
}
export interface Reference {
  cvr: number;
  cvr_pct: number;
  scope: "campaign" | "account";
}
export type FindingLevel = "confirmed" | "watch";
export interface FindingTarget {
  id: string;
  external_id: string;
  text: string;
  match_type: string;
}
export interface FindingTerm {
  id: string;
  external_id: string;
  term: string;
}
export interface WastedTarget {
  target: FindingTarget;
  campaign_name: string;
  totals: Totals;
  level: FindingLevel;
  chance_of_zero_orders_pct: number;
  reference: Reference;
  projected_monthly_spend_paise: number;
}
export interface UnderperformingTarget {
  target: FindingTarget;
  campaign_name: string;
  totals: Totals;
  kpis: Kpis;
  chance_pct: number;
  reference: Reference;
}
export interface NegativeCandidate {
  term: FindingTerm;
  parent_target_text: string | null;
  campaign_name: string;
  totals: Totals;
  level: FindingLevel;
  chance_of_zero_orders_pct: number;
  reference: Reference;
  projected_monthly_spend_paise: number;
}
export interface Opportunity {
  term: FindingTerm;
  parent_target_text: string | null;
  campaign_name: string;
  totals: Totals;
  kpis: Kpis;
  cvr_lower_bound_pct: number;
  strength: "strong" | "moderate";
  reference: Reference;
}
export interface Findings {
  period: Span;
  truncated_days: number;
  target_acos_pct: number;
  wasted_targets: WastedTarget[];
  underperforming_targets: UnderperformingTarget[];
  negative_candidates: NegativeCandidate[];
  opportunities: Opportunity[];
  low_data_targets: number;
  low_data_terms: number;
  unjudged_campaigns: number;
  counts: { wasted_targets: number; underperforming_targets: number; negative_candidates: number; opportunities: number; limit_per_list: number };
}

export type AnomalyMetric = "cvr" | "cpc" | "spend";
export interface AnomalyEpisode {
  campaign: { id: string; name: string };
  metric: AnomalyMetric;
  direction: "up" | "down";
  severity: "alert" | "watch";
  status: "ongoing" | "subsided";
  onset: string;
  detected_on: string;
  alert_on: string | null;
  last_day: string;
  reference_value: number;
  observed_value: number;
  relative_change_pct: number;
}
export interface AcosContext {
  previous_pct: number;
  current_pct: number;
  change_points: number;
  main_driver: { name: string; contribution_points: number } | null;
}
export interface Anomalies {
  monitor: Span;
  reference: Span;
  episodes: AnomalyEpisode[];
  skipped: { campaign_external_id: string; metric: AnomalyMetric; reason: string }[];
  acos_context: Record<string, AcosContext | null>;
}

export interface ForecastPoint {
  date: string;
  point: number;
  lower: number;
  upper: number;
}
export interface StoredForecast {
  id: string;
  metric: "spend" | "sales";
  model: string;
  used_baseline: boolean;
  train_days: number;
  train_end: string;
  horizon_start: string;
  horizon_end: string;
  points: ForecastPoint[];
  params: { reason?: string; interval_level?: number; cv_coverage?: number | null };
  data_source: string;
  created_at: string;
}
export interface AccountForecast {
  spend: StoredForecast;
  sales: StoredForecast;
  acos_pct: number | null;
  acos_note: string;
}

export interface AuditEntry {
  id: string;
  actor: string;
  action: string;
  entity: { type: string | null; id: string | null };
  request_id: string | null;
  created_at: string;
}
