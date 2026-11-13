"""Runs the scheduled jobs for every organization, one organization and one job at a time.

Each (organization, job) pair runs in its own tenant session and transaction, so a failure in one
cannot roll back or block another, and each session is isolated by RLS exactly like a request. An
advisory lock per pair stops two overlapping runs (for example two instances, or cron firing while
a slow run is still going) from doing the same work twice. A job's exception is logged without its
message (messages can echo data) and reported as ``failed``; the run carries on.
"""

import hashlib
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.application.jobs.maintenance import JobOutcome, expire_overdue, refresh_forecast
from app.domain.tenant import TenantContext
from app.infrastructure.db.audit_store import SqlAuditStore
from app.infrastructure.db.forecast_store import SqlForecastStore
from app.infrastructure.db.models import Organization
from app.infrastructure.db.performance_reader import SqlPerformanceReader
from app.infrastructure.db.recommendation_store import SqlRecommendationStore
from app.infrastructure.db.session import maintenance_session, tenant_session
from app.logging import exception_details

log = structlog.get_logger()
JOBS = ("expire", "forecast")


async def list_organization_ids(engine: AsyncEngine, app_role: str | None) -> list[UUID]:
    async with maintenance_session(engine, app_role) as session:
        rows = await session.scalars(select(Organization.id).order_by(Organization.id))
        return list(rows)


def _lock_key(org: UUID, job: str) -> int:
    digest = hashlib.sha256(f"adpilot:{job}:{org}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


async def _run_one(
    session: AsyncSession, org: UUID, job: str, now: datetime, force: bool
) -> JobOutcome:
    locked = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _lock_key(org, job)}
    )
    if not locked:
        return JobOutcome(job, org, "skipped", "another run of this job holds the lock")
    if job == "expire":
        return await expire_overdue(SqlRecommendationStore(session, org), org, now)
    return await refresh_forecast(
        SqlPerformanceReader(session, org),
        SqlForecastStore(session, org),
        SqlAuditStore(session, org),
        org,
        now,
        force=force,
    )


async def run_jobs(
    engine: AsyncEngine,
    app_role: str | None,
    jobs: Sequence[str],
    now: datetime,
    *,
    org_ids: Sequence[UUID] | None = None,
    force: bool = False,
) -> list[JobOutcome]:
    unknown = set(jobs) - set(JOBS)
    if unknown:
        raise ValueError(f"unknown job(s): {sorted(unknown)}")
    orgs = list(org_ids) if org_ids is not None else await list_organization_ids(engine, app_role)
    outcomes: list[JobOutcome] = []
    for org in orgs:
        for job in jobs:
            try:
                async with tenant_session(engine, TenantContext(org), app_role) as session:
                    outcome = await _run_one(session, org, job, now, force)
            except Exception as exc:  # one failure must not stop the other organizations
                log.error("job_failed", job=job, org_id=str(org), **exception_details(exc))
                outcome = JobOutcome(job, org, "failed", type(exc).__name__)
            log.info("job_finished", job=job, org_id=str(org), status=outcome.status)
            outcomes.append(outcome)
    return outcomes
