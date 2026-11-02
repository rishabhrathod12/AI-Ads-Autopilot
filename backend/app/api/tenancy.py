"""Who is asking, and which tenant's data may they see.

DEVELOPMENT ONLY: until real authentication lands (ADR 0008; required before any non-local
deployment and before the approval workflow), the tenant is taken from an ``X-Dev-Org-Id`` header.
``create_app`` refuses to start in production, so this can never serve real traffic. Everything
downstream receives a ``TenantContext`` and is isolated by RLS regardless of how it was resolved.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request

from app.application.ports.forecast_store import ForecastStore
from app.application.ports.performance_reader import PerformanceReader
from app.config import Settings
from app.domain.tenant import TenantContext
from app.infrastructure.db.forecast_store import SqlForecastStore
from app.infrastructure.db.performance_reader import SqlPerformanceReader
from app.infrastructure.db.session import tenant_session

DEV_ORG_HEADER = "X-Dev-Org-Id"


@dataclass(frozen=True)
class Scope:
    """Everything a request may touch, already bound to one tenant."""

    organization_id: UUID
    reader: PerformanceReader
    forecasts: ForecastStore


def resolve_tenant(request: Request) -> TenantContext:
    settings: Settings = request.app.state.settings
    if settings.app_env == "production":  # belt and braces: create_app also refuses to start
        raise HTTPException(status_code=401, detail="Authentication required.")
    raw = request.headers.get(DEV_ORG_HEADER)
    if raw is None:
        raise HTTPException(status_code=401, detail=f"Missing {DEV_ORG_HEADER} header (dev only).")
    try:
        return TenantContext(UUID(raw))
    except ValueError:
        raise HTTPException(status_code=401, detail=f"Invalid {DEV_ORG_HEADER} header.") from None


async def get_scope(
    request: Request, tenant: TenantContext = Depends(resolve_tenant)
) -> AsyncIterator[Scope]:
    engine = request.app.state.engine
    if engine is None:
        raise HTTPException(status_code=503, detail="Database not configured.")
    settings: Settings = request.app.state.settings
    async with tenant_session(engine, tenant, settings.db_app_role) as session:
        org = tenant.organization_id
        yield Scope(org, SqlPerformanceReader(session, org), SqlForecastStore(session, org))
