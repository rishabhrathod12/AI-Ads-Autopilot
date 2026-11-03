"""The performance report: built by code from the tested analytics, rendered without any model.

Every figure is copied from the analytics results, so it is traceable by construction. Nothing
here calls an LLM, writes data, or changes an ad. Seller-controlled text (campaign and keyword
names) is escaped for each output format; in CSV a leading ``= + - @`` is neutralised so a
spreadsheet never runs a seller-written string as a formula.
"""

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.application.analytics.anomalies import detect_anomalies
from app.application.analytics.campaigns import campaign_overview
from app.application.analytics.dashboard import dashboard_summary
from app.application.analytics.keywords import keyword_analysis
from app.application.analytics.thresholds import DEFAULT_TARGET_ACOS_PCT
from app.application.ports.performance_reader import PerformanceReader
from app.domain.kpi.metrics import Kpis, MetricTotals
from app.domain.kpi.periods import DateRange

NA = "n/a"
CAMPAIGN_COLUMNS = (
    "campaign",
    "state",
    "health",
    "spend_rupees",
    "ad_sales_rupees",
    "acos_pct",
    "roas",
    "orders",
    "clicks",
    "spend_share_pct",
)


@dataclass(frozen=True)
class CampaignLine:
    name: str
    state: str
    health: str
    spend_paise: int
    sales_paise: int
    acos_pct: Decimal | None
    roas: Decimal | None
    orders: int
    clicks: int
    spend_share_pct: Decimal | None


@dataclass(frozen=True)
class WastedLine:
    keyword: str
    campaign: str
    clicks: int
    spend_paise: int
    level: str


@dataclass(frozen=True)
class AnomalyLine:
    campaign: str
    metric: str
    direction: str
    severity: str
    status: str
    onset: date
    relative_change_pct: float


@dataclass(frozen=True)
class PerformanceReport:
    requested: DateRange
    period: DateRange  # complete (mature) days actually used
    truncated_days: int
    target_acos_pct: Decimal
    data_sources: tuple[str, ...]
    synthetic: bool
    as_of: datetime | None
    totals: MetricTotals
    kpis: Kpis
    campaigns: tuple[CampaignLine, ...]
    wasted: tuple[WastedLine, ...]
    wasted_total: int  # how many wasted targets exist before the top-N cut
    anomalies: tuple[AnomalyLine, ...]

    @property
    def notes(self) -> tuple[str, ...]:
        notes = ["Sales are ad-attributed only; organic and total sales are not available."]
        if self.synthetic:
            notes.insert(0, "MOCK (synthetic) data, not live Amazon data.")
        if self.truncated_days:
            notes.append(
                f"The latest {self.truncated_days} day(s) are still inside Amazon's attribution "
                "window and are excluded."
            )
        return tuple(notes)


async def build_performance_report(
    reader: PerformanceReader,
    requested: DateRange,
    *,
    target_acos_pct: Decimal = DEFAULT_TARGET_ACOS_PCT,
    top_n: int = 5,
) -> PerformanceReport:
    summary = await dashboard_summary(reader, requested)
    campaigns = await campaign_overview(reader, requested, target_acos_pct=target_acos_pct)
    keywords = await keyword_analysis(reader, requested, target_acos_pct=target_acos_pct)
    anomalies = await detect_anomalies(reader)

    # Everything uses the same complete days: the dashboard's own totals include the days still
    # inside the attribution window, which the campaign table excludes.
    totals = summary.comparison.current_totals if summary.comparison else summary.totals
    lines = tuple(
        CampaignLine(
            r.info.name,
            r.info.state,
            r.health.status.value,
            r.totals.spend_paise,
            r.totals.sales_paise,
            r.kpis.acos_pct,
            r.kpis.roas,
            r.totals.orders,
            r.totals.clicks,
            r.spend_share_pct,
        )
        for r in sorted(campaigns.rows, key=lambda r: -r.totals.spend_paise)
    )
    wasted = tuple(
        WastedLine(
            w.target.text,
            w.campaign_name,
            w.totals.clicks,
            w.totals.spend_paise,
            w.level.value,
        )
        for w in sorted(keywords.wasted_targets, key=lambda w: -w.totals.spend_paise)[:top_n]
    )
    episodes = tuple(
        AnomalyLine(
            e.campaign.name,
            e.metric.value,
            e.direction.value,
            e.severity.value,
            e.status.value,
            e.onset,
            e.relative_change_pct,
        )
        for e in anomalies.episodes
    )
    sources = tuple(sorted(s.value for s in summary.provenance.data_sources))
    return PerformanceReport(
        requested=requested,
        period=campaigns.period,
        truncated_days=campaigns.truncated_days,
        target_acos_pct=target_acos_pct,
        data_sources=sources,
        synthetic="MOCK" in sources,
        as_of=summary.provenance.as_of,
        totals=totals,
        kpis=Kpis.from_totals(totals),
        campaigns=lines,
        wasted=wasted,
        wasted_total=len(keywords.wasted_targets),
        anomalies=episodes,
    )


# ---- formatting ------------------------------------------------------------------------------
def rupees(paise: int | Decimal | None) -> str:
    if paise is None:
        return NA
    return f"{(Decimal(paise) / 100).quantize(Decimal('0.01'), ROUND_HALF_UP):.2f}"


def ratio(value: Decimal | float | None, places: int = 2) -> str:
    if value is None:
        return NA
    return f"{Decimal(str(value)).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP):.{places}f}"


def csv_safe(text: str) -> str:
    """Stop a spreadsheet from running a seller-written string as a formula."""
    cleaned = "".join(ch for ch in text if ch >= " " and ch != "\x7f")
    return "'" + cleaned if cleaned[:1] in ("=", "+", "-", "@") else cleaned


def md_safe(text: str) -> str:
    cleaned = "".join(ch for ch in text if ch >= " " and ch != "\x7f")
    for ch in ("\\", "|", "`", "*", "_", "<", ">", "[", "]"):
        cleaned = cleaned.replace(ch, "\\" + ch)
    return cleaned


def campaigns_csv(report: PerformanceReport) -> str:
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CAMPAIGN_COLUMNS)
    for c in report.campaigns:
        writer.writerow(
            [
                csv_safe(c.name),
                csv_safe(c.state),
                c.health,
                rupees(c.spend_paise),
                rupees(c.sales_paise),
                ratio(c.acos_pct),
                ratio(c.roas),
                c.orders,
                c.clicks,
                ratio(c.spend_share_pct),
            ]
        )
    return out.getvalue()


def report_markdown(report: PerformanceReport) -> str:
    k, t = report.kpis, report.totals
    p = report.period
    lines = [
        "# PPC performance report",
        "",
        f"Period: {p.start} to {p.end} (complete days; requested "
        f"{report.requested.start} to {report.requested.end})",
        f"Data sources: {', '.join(report.data_sources) or NA}"
        + (f" - as of {report.as_of:%Y-%m-%d %H:%M} UTC" if report.as_of else ""),
        f"Target ACOS: {ratio(report.target_acos_pct, 1)}%",
        "",
        *[f"> {n}" for n in report.notes],
        "",
        "## Account",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Ad spend | {rupees(t.spend_paise)} INR |",
        f"| Ad-attributed sales | {rupees(t.sales_paise)} INR |",
        f"| ACOS | {ratio(k.acos_pct)}% |" if k.acos_pct is not None else "| ACOS | n/a |",
        f"| ROAS | {ratio(k.roas)} |",
        f"| Orders | {t.orders} |",
        f"| Clicks | {t.clicks} |",
        f"| CPC | {rupees(k.cpc_paise)} INR |",
        f"| Conversion rate | {ratio(k.cvr_pct)}% |",
        "",
        "## Campaigns (by spend)",
        "",
        "| Campaign | Health | Spend INR | Sales INR | ACOS % | Orders |",
        "|---|---|---|---|---|---|",
        *[
            f"| {md_safe(c.name)} | {c.health} | {rupees(c.spend_paise)} | "
            f"{rupees(c.sales_paise)} | {ratio(c.acos_pct)} | {c.orders} |"
            for c in report.campaigns
        ],
        "",
        f"## Keywords wasting spend (top {len(report.wasted)} of {report.wasted_total})",
        "",
    ]
    if report.wasted:
        lines += [
            "| Keyword | Campaign | Clicks | Spend INR | Evidence |",
            "|---|---|---|---|---|",
            *[
                f"| {md_safe(w.keyword)} | {md_safe(w.campaign)} | {w.clicks} | "
                f"{rupees(w.spend_paise)} | {w.level} |"
                for w in report.wasted
            ],
        ]
    else:
        lines.append("None found.")
    lines += ["", "## Sustained shifts (anomalies)", ""]
    if report.anomalies:
        lines += [
            "| Campaign | Metric | Direction | Severity | Status | Since | Change % |",
            "|---|---|---|---|---|---|---|",
            *[
                f"| {md_safe(a.campaign)} | {a.metric} | {a.direction} | {a.severity} | "
                f"{a.status} | {a.onset} | {ratio(a.relative_change_pct, 1)} |"
                for a in report.anomalies
            ],
        ]
    else:
        lines.append("None detected.")
    lines += [
        "",
        "Findings are observations, not instructions. This report changes nothing in any campaign.",
    ]
    return "\n".join(lines) + "\n"
