"""Read-only analytics API (plus forecast creation, which stores a run but changes no ads).

All routes are tenant-scoped through ``get_scope``. Analytics never mutates advertising data.
"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from app.api.serialize import envelope, to_jsonable
from app.api.tenancy import Scope, get_scope
from app.application.analytics.anomalies import detect_anomalies
from app.application.analytics.campaigns import campaign_changes, campaign_overview
from app.application.analytics.dashboard import NoDataError, dashboard_summary
from app.application.analytics.keyword_tables import SortKey, search_term_table, target_table
from app.application.analytics.keywords import keyword_analysis
from app.application.analytics.thresholds import DEFAULT_TARGET_ACOS_PCT, DEFAULT_WINDOW_DAYS
from app.application.forecasting.records import save_account_forecast
from app.application.forecasting.reports import forecast_account
from app.application.ports.performance_reader import Provenance
from app.application.reporting.report import (
    build_performance_report,
    campaigns_csv,
    report_markdown,
)
from app.domain.kpi.metrics import Kpis, acos_pct
from app.domain.kpi.periods import DateRange

router = APIRouter(prefix="/api/v1", tags=["analytics"])

MAX_PERIOD_DAYS = 366
FromParam = Annotated[date | None, Query(alias="from", description="First day (inclusive)")]
ToParam = Annotated[date | None, Query(alias="to", description="Last day (inclusive)")]
TargetAcos = Annotated[Decimal, Query(gt=0, le=1000, description="Target ACOS, in percent")]
Limit = Annotated[int, Query(ge=1, le=200)]


async def period_of(scope: Scope, date_from: date | None, date_to: date | None) -> DateRange:
    fresh = await scope.reader.freshness()
    if fresh.latest_date is None:
        raise NoDataError("no advertising data")
    end = date_to or fresh.latest_date
    start = date_from or end - timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    if start > end:
        raise HTTPException(422, "`from` must not be after `to`.")
    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        raise HTTPException(422, f"Period too long (max {MAX_PERIOD_DAYS} days).")
    return DateRange(start, end)


@router.get("/dashboard/summary")
async def get_dashboard_summary(
    date_from: FromParam = None, date_to: ToParam = None, scope: Scope = Depends(get_scope)
) -> dict[str, Any]:
    period = await period_of(scope, date_from, date_to)
    result = await dashboard_summary(scope.reader, period)
    return envelope(result, result.provenance, result.freshness, drop=("trend",))


@router.get("/dashboard/trends")
async def get_dashboard_trends(
    date_from: FromParam = None, date_to: ToParam = None, scope: Scope = Depends(get_scope)
) -> dict[str, Any]:
    period = await period_of(scope, date_from, date_to)
    result = await dashboard_summary(scope.reader, period)
    points = [
        {
            "date": p.day,
            "is_mature": p.is_mature,
            "totals": p.totals,
            "kpis": Kpis.from_totals(p.totals),
        }
        for p in result.trend
    ]
    body = envelope({"period": period, "points": points}, result.provenance, result.freshness)
    return body


@router.get("/campaigns")
async def get_campaigns(
    date_from: FromParam = None,
    date_to: ToParam = None,
    target_acos: TargetAcos = DEFAULT_TARGET_ACOS_PCT,
    scope: Scope = Depends(get_scope),
) -> dict[str, Any]:
    period = await period_of(scope, date_from, date_to)
    result = await campaign_overview(scope.reader, period, target_acos_pct=target_acos)
    return envelope(result, result.provenance, result.freshness)


@router.get("/campaigns/changes")
async def get_campaign_changes(
    date_from: FromParam = None, date_to: ToParam = None, scope: Scope = Depends(get_scope)
) -> dict[str, Any]:
    period = await period_of(scope, date_from, date_to)
    changes = await campaign_changes(scope.reader, period)
    fresh = await scope.reader.freshness()
    prov = await scope.reader.provenance(period)
    return envelope({"campaigns": changes}, prov, fresh)


@router.get("/keywords")
async def get_keywords(
    date_from: FromParam = None,
    date_to: ToParam = None,
    sort: SortKey = "spend",
    limit: Limit = 50,
    scope: Scope = Depends(get_scope),
) -> dict[str, Any]:
    period = await period_of(scope, date_from, date_to)
    result = await target_table(scope.reader, period, sort=sort, limit=limit)
    return envelope(result, result.provenance, result.freshness)


@router.get("/search-terms")
async def get_search_terms(
    date_from: FromParam = None,
    date_to: ToParam = None,
    sort: SortKey = "spend",
    limit: Limit = 50,
    scope: Scope = Depends(get_scope),
) -> dict[str, Any]:
    period = await period_of(scope, date_from, date_to)
    result = await search_term_table(scope.reader, period, sort=sort, limit=limit)
    return envelope(result, result.provenance, result.freshness)


@router.get("/keywords/findings")
async def get_keyword_findings(
    date_from: FromParam = None,
    date_to: ToParam = None,
    target_acos: TargetAcos = DEFAULT_TARGET_ACOS_PCT,
    limit: Limit = 25,
    scope: Scope = Depends(get_scope),
) -> dict[str, Any]:
    period = await period_of(scope, date_from, date_to)
    full = await keyword_analysis(scope.reader, period, target_acos_pct=target_acos)
    trimmed = replace(
        full,
        wasted_targets=full.wasted_targets[:limit],
        underperforming_targets=full.underperforming_targets[:limit],
        negative_candidates=full.negative_candidates[:limit],
        opportunities=full.opportunities[:limit],
    )
    body = envelope(trimmed, full.provenance, full.freshness)
    body["data"]["counts"] = {
        "wasted_targets": len(full.wasted_targets),
        "underperforming_targets": len(full.underperforming_targets),
        "negative_candidates": len(full.negative_candidates),
        "opportunities": len(full.opportunities),
        "limit_per_list": limit,
    }
    return body


@router.get("/anomalies")
async def get_anomalies(scope: Scope = Depends(get_scope)) -> dict[str, Any]:
    result = await detect_anomalies(scope.reader)
    return envelope(result, result.provenance, result.freshness)


@router.post("/forecasts/account", status_code=201)
async def create_account_forecast(scope: Scope = Depends(get_scope)) -> dict[str, Any]:
    """Backtest, select and store a 14-day spend and sales forecast (takes several seconds)."""
    result = await forecast_account(scope.reader)
    ids = await save_account_forecast(scope.forecasts, result)
    body = envelope(result, result.provenance, result.freshness)
    body["data"]["run_ids"] = to_jsonable(ids)
    return body


@router.get("/forecasts/account")
async def get_account_forecast(scope: Scope = Depends(get_scope)) -> dict[str, Any]:
    spend = await scope.forecasts.latest("account", None, "spend")
    sales = await scope.forecasts.latest("account", None, "sales")
    if spend is None or sales is None:
        raise HTTPException(404, "No stored forecast yet. POST /api/v1/forecasts/account first.")
    fresh = await scope.reader.freshness()
    prov = Provenance(frozenset({spend.data_source}), spend.input_as_of)
    spend_total = round(sum(p["point"] for p in spend.points))
    sales_total = round(sum(p["point"] for p in sales.points))
    body = envelope({"spend": spend, "sales": sales}, prov, fresh)
    body["data"]["acos_pct"] = to_jsonable(acos_pct(spend_total, sales_total))
    body["data"]["acos_note"] = "Derived from the stored spend and sales forecasts."
    return body


@router.get("/reports/performance")
async def get_performance_report(
    date_from: FromParam = None,
    date_to: ToParam = None,
    target_acos_pct: TargetAcos = DEFAULT_TARGET_ACOS_PCT,
    report_format: Annotated[
        Literal["json", "markdown", "csv"], Query(alias="format", description="Output format")
    ] = "json",
    scope: Scope = Depends(get_scope),
) -> Any:
    """A deterministic report built by code (no model). ``csv`` is the campaign table."""
    period = await period_of(scope, date_from, date_to)
    report = await build_performance_report(scope.reader, period, target_acos_pct=target_acos_pct)
    if report_format == "markdown":
        return PlainTextResponse(report_markdown(report), media_type="text/markdown")
    if report_format == "csv":
        return PlainTextResponse(
            campaigns_csv(report),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="campaigns.csv"'},
        )
    return {"data": to_jsonable(report), "notes": list(report.notes)}
