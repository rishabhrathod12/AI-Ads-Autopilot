# Agent Architecture

## 1. Principles
- Deterministic Python computes; LLMs plan, select tools, explain.
- Specialists **propose**; they never execute changes.
- Every tool is tenant-scoped via a `TenantContext` the agent cannot override.
- Typed input/output (Pydantic) at every node; traceable steps; honest uncertainty.
- Retrieved documents and tool outputs are data, never instructions.

## 2. Build order
M4: single tool-calling assistant over the analytics tools. M5: refactor into the LangGraph graph once tools are reliable.

## 3. Graph
```
START -> Supervisor(plan) -> route -> specialist(s) (parallel when independent)
      -> Validator (code) -> Supervisor(synthesize) -> END
                   | fail -> retry once / degrade honestly ("insufficient data")
Writes: recommendation -> human approval interrupt (checkpointed) -> gated execution
```

## 4. State
```
AgentState { tenant_ctx, request, plan, task_results[], evidence[], validation, final, budget{steps,tokens,tool_calls} }
```
Checkpointer: PostgreSQL. Duplicate identical tool calls are cached/blocked.

## 5. Agents
| Agent | Responsibility | Inputs | Outputs | Tools | Errors | Evaluation |
|---|---|---|---|---|---|---|
| Supervisor | Classify, plan, delegate, synthesize with citations | user request, tenant ctx | `ExecutionPlan`, `FinalAnswer` | capability list, delegation | step budget; may answer "insufficient data" | routing accuracy; no unsupported claims |
| Campaign Analysis | KPIs, trends, anomalies, health | date range, campaign ids | `CampaignAnalysis` | `get_campaign_metrics`, `compare_periods`, `detect_anomalies` | empty range -> `no_data` | numbers equal KPI engine |
| Keyword Intelligence | Keyword/search-term analysis, wasted spend, negatives, opportunities | date range, scope | `KeywordFindings` | `get_keyword_metrics`, `get_search_terms`, `find_wasted_spend` | min-click gates; low-confidence flag | precision/recall on labeled mock cases |
| Bid Optimization | Target-ACOS bid proposals | keywords, target ACOS | `BidRecommendation[]` | `get_keyword_history`, `calc_bid_adjustment` | min-data rule; change caps | rules respected; backtest |
| Budget Allocation | Utilization, reallocation, scenarios | campaigns, total budget | `BudgetRecommendation[]` | `get_budget_utilization`, `simulate_budget_scenario` | budget conserved | constraints satisfied |
| Performance Prediction | Forecast spend/sales/ACOS with intervals | series, horizon | `Forecast` + model metrics | `run_forecast` | refuses on short history | MAE/RMSE/MAPE vs naive baseline |
| PPC Reporting | Narrative summaries, structured reports | validated numbers | `ReportDocument` | `build_report_data`, `render_report` | reads validated numbers only | every number traceable |

No further agents. Wasted spend and negative keywords stay in Keyword Intelligence.

## 6. Validator (non-LLM)
Schema validity; evidence present and resolvable; numbers in prose match tool results; guardrails (bid change caps, min clicks/orders, budget conservation); tenant match. Failure -> retry once or degrade.

## 7. Human-in-the-loop
Recommendations are persisted as `pending`. Only an authorized human approval moves them forward. Execution is a separate, gated service, outside the agent graph.

## 8. Evaluation plan (M7)
Golden-question set with expected tool calls and numbers; faithfulness check; routing accuracy; guardrail violation count (target 0); cost/latency per run.
