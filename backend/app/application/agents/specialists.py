"""The specialists: each is a named, bounded view onto the existing read-only analytics tools.

A specialist gathers evidence; it never writes the answer (the synthesizer does) and never gets a
tool outside its list. Bid and Budget specialists belong to M6 because they produce
recommendations that need the approval workflow (ADR 0018).
"""

from dataclasses import dataclass
from typing import Literal, get_args

SpecialistName = Literal[
    "campaign_analysis", "keyword_intelligence", "performance_prediction", "ppc_reporting"
]
SPECIALIST_NAMES: tuple[SpecialistName, ...] = get_args(SpecialistName)

KNOWLEDGE = "search_knowledge"


@dataclass(frozen=True)
class Specialist:
    name: SpecialistName
    summary: str  # shown to the supervisor when it plans
    tools: tuple[str, ...]


SPECIALISTS: dict[str, Specialist] = {
    s.name: s
    for s in (
        Specialist(
            "campaign_analysis",
            "Account and campaign performance: KPIs, trends, why ACOS changed, which campaigns "
            "need attention, sudden shifts (anomalies). Also definitions of PPC terms.",
            (
                "get_dashboard_summary",
                "get_campaign_overview",
                "get_campaign_changes",
                "get_anomalies",
                KNOWLEDGE,
            ),
        ),
        Specialist(
            "keyword_intelligence",
            "Keywords and search terms: wasted spend, underperformers, negative-keyword "
            "candidates, opportunities, top keywords. Also keyword optimisation guidance.",
            ("get_keyword_findings", "get_top_keywords", KNOWLEDGE),
        ),
        Specialist(
            "performance_prediction",
            "The stored 14-day forecast of spend, sales and ACOS and how it was chosen.",
            ("get_account_forecast", KNOWLEDGE),
        ),
        Specialist(
            "ppc_reporting",
            "Overview or report requests ('summarise my account', 'weekly report', 'give me the "
            "full picture'): collects the account summary, campaigns, keyword findings, "
            "anomalies and forecast in one pass. Use it alone for these; do not add others.",
            (
                "get_dashboard_summary",
                "get_campaign_overview",
                "get_keyword_findings",
                "get_anomalies",
                "get_account_forecast",
            ),
        ),
    )
}
