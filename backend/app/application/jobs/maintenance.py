"""Scheduled maintenance jobs for one organization. Pure use cases over ports; no database code.

Both jobs are safe to run any number of times: expiring only touches recommendations that are
still pending and past their expiry, and a forecast is refreshed only when the stored one is old.
Every change is audited under the actor ``system:jobs``. Neither job ever calls an ad provider.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from app.application.analytics.dashboard import NoDataError
from app.application.audit import audit_entry
from app.application.forecasting.records import save_account_forecast
from app.application.forecasting.reports import forecast_account
from app.application.forecasting.service import InsufficientHistoryError, NoTrafficError
from app.application.ports.audit_store import AuditStore
from app.application.ports.forecast_store import ForecastStore
from app.application.ports.performance_reader import PerformanceReader
from app.application.ports.recommendation_store import RecommendationStore
from app.domain.kpi.periods import NoMatureDataError
from app.domain.recommendations import Status, is_expired

SYSTEM_ACTOR = "system:jobs"
FORECAST_MAX_AGE = timedelta(hours=20)  # a daily schedule refreshes once per run, not twice
EXPIRE_BATCH = 1000  # per run; the next run continues if there were more


@dataclass(frozen=True)
class JobOutcome:
    job: str
    organization_id: UUID
    status: Literal["done", "skipped", "failed"]
    detail: str


async def expire_overdue(
    store: RecommendationStore, org: UUID, now: datetime, *, request_id: str | None = None
) -> JobOutcome:
    """Mark pending recommendations past their expiry as expired, with an audit entry each."""
    expired = 0
    for rec in await store.list_recommendations(Status.PENDING, limit=EXPIRE_BATCH):
        if not is_expired(rec, now):
            continue
        if await store.transition(rec.id, expected=Status.PENDING, new=Status.EXPIRED):
            await store.add_audit(
                audit_entry(
                    org=org,
                    actor=SYSTEM_ACTOR,
                    action="recommendation.expired",
                    entity_type="recommendation",
                    entity_id=str(rec.id),
                    now=now,
                    request_id=request_id,
                    before={"status": Status.PENDING.value},
                    after={"status": Status.EXPIRED.value, "by": "scheduled job"},
                )
            )
            expired += 1
    return JobOutcome("expire", org, "done", f"{expired} recommendation(s) expired")


async def refresh_forecast(
    reader: PerformanceReader,
    forecasts: ForecastStore,
    audit: AuditStore,
    org: UUID,
    now: datetime,
    *,
    force: bool = False,
    request_id: str | None = None,
) -> JobOutcome:
    """Store a fresh 14-day account forecast unless one is recent or there is no usable data."""
    latest = await forecasts.latest("account", None, "spend")
    if latest is not None and not force and now - latest.created_at < FORECAST_MAX_AGE:
        return JobOutcome("forecast", org, "skipped", "a recent forecast already exists")
    try:
        result = await forecast_account(reader)
    except (NoDataError, NoMatureDataError, InsufficientHistoryError, NoTrafficError) as exc:
        return JobOutcome("forecast", org, "skipped", f"{type(exc).__name__}: {exc}")
    ids = await save_account_forecast(forecasts, result)
    await audit.add_audit(
        audit_entry(
            org=org,
            actor=SYSTEM_ACTOR,
            action="forecast.created",
            entity_type="forecast",
            entity_id="account",
            now=now,
            request_id=request_id,
            after={"run_ids": {metric: str(run_id) for metric, run_id in ids.items()}},
        )
    )
    return JobOutcome("forecast", org, "done", "stored a new 14-day forecast")
