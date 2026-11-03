# ADR 0020: Deterministic performance report, and a reporting specialist

Status: Accepted (M5)

## Context
The agent plan lists a PPC Reporting agent that "reads validated numbers only". Reports are also an owner
requirement (PDF + CSV, decision 12). Letting a model write a report invites invented or mis-rounded figures.

## Decision
- **The report is built by code, with no model.** `build_performance_report` copies figures from the tested
  analytics (dashboard, campaigns, keyword findings, anomalies) over one consistent period: the complete (mature)
  days. Rendered as JSON, Markdown, or CSV (the campaign table) at `GET /api/v1/reports/performance`.
- **Output safety:** seller-controlled names are escaped for Markdown, and a leading `= + - @` in CSV is neutralised so
  a spreadsheet never runs it as a formula. Undefined ratios print `n/a`, never 0.
- **A fourth specialist, `ppc_reporting`, serves conversational report requests.** It only gathers the standard bundle
  of evidence; the synthesizer's narrative still passes the faithfulness validator (ADR 0015). It is not the
  deterministic report and does not replace it.
- **PDF is not built.** It needs a rendering dependency that cannot be verified here; Markdown renders to PDF
  outside the app for now.

## Consequences
- Report numbers are traceable by construction and tested equal to the analytics.
- A mistake caught while building: the dashboard totals include days still inside the attribution window, but the
  campaign table does not, so a first draft showed an account total larger than the sum of its campaigns. The report now
  uses the complete-day totals throughout (tested).
- Adding a fourth specialist changes supervisor routing. The earlier pre-registered graph evaluation ran with three
  specialists, so its results do not describe this routing; M5 needs a new evaluation before it is called done.
