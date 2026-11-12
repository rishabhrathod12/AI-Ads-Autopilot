"""Who is asking, and which tenant's data may they see.

Two modes, chosen by configuration and never by the request:

* **Authenticated** (``AUTH_JWT_ISSUER``, ``AUTH_JWT_AUDIENCE`` and ``AUTH_JWKS_URL`` all set): a
  bearer JWT is required. ``X-Org-Id`` only *selects* which of the caller's organizations to act in;
  the role comes from their membership row, and a non-member gets 403. The development header is
  ignored entirely.
* **Development** (not configured, never in production): the tenant comes from ``X-Dev-Org-Id`` and
  the role from the optional ``X-Dev-Role`` (default analyst). ``create_app`` refuses to start in
  production without full auth configuration, and this module refuses as well.

Everything downstream receives a ``TenantContext`` and is isolated by RLS however it was resolved.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import structlog
from fastapi import Depends, HTTPException, Request

from app.application.ports.audit_store import AuditStore
from app.application.ports.auth import AuthError, MembershipDirectory, TokenVerifier
from app.application.ports.forecast_store import ForecastStore
from app.application.ports.performance_reader import PerformanceReader
from app.config import Settings
from app.domain.auth import Principal, Role
from app.domain.tenant import TenantContext
from app.infrastructure.auth.membership import SqlMembershipDirectory
from app.infrastructure.db.audit_store import SqlAuditStore
from app.infrastructure.db.forecast_store import SqlForecastStore
from app.infrastructure.db.performance_reader import SqlPerformanceReader
from app.infrastructure.db.session import tenant_session

DEV_ORG_HEADER = "X-Dev-Org-Id"
DEV_ROLE_HEADER = "X-Dev-Role"
ORG_HEADER = "X-Org-Id"


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid or missing credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


@dataclass(frozen=True)
class Scope:
    """Everything a request may touch, already bound to one tenant."""

    organization_id: UUID
    reader: PerformanceReader
    forecasts: ForecastStore
    audit: AuditStore


def resolve_tenant(request: Request) -> TenantContext:
    """Development-only tenant from a header (see module docstring)."""
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


def _bearer_token(request: Request) -> str:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized()
    return token.strip()


def _dev_principal(request: Request) -> Principal:
    tenant = resolve_tenant(request)
    role = Role.ANALYST
    raw_role = request.headers.get(DEV_ROLE_HEADER)
    if raw_role is not None:
        parsed = Role.parse(raw_role)
        if parsed is None:
            raise HTTPException(status_code=401, detail=f"Invalid {DEV_ROLE_HEADER} header.")
        role = parsed
    return Principal("dev", tenant.organization_id, role)


async def _verified_principal(request: Request, verifier: TokenVerifier) -> Principal:
    try:
        claims = await verifier.verify(_bearer_token(request))
    except AuthError:
        raise _unauthorized() from None
    try:
        org = UUID(request.headers.get(ORG_HEADER, ""))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Missing or invalid {ORG_HEADER}.") from None
    directory: MembershipDirectory | None = request.app.state.membership_directory
    if directory is None:
        engine = request.app.state.engine
        if engine is None:
            raise HTTPException(status_code=503, detail="Database not configured.")
        settings: Settings = request.app.state.settings
        directory = SqlMembershipDirectory(engine, settings.db_app_role)
    role = await directory.role_for(claims.subject, org)
    if role is None:
        raise HTTPException(status_code=403, detail="Not a member of this organization.")
    return Principal(claims.subject, org, role)


async def current_principal(request: Request) -> Principal:
    verifier: TokenVerifier | None = request.app.state.token_verifier
    principal = (
        _dev_principal(request)
        if verifier is None
        else await _verified_principal(request, verifier)
    )
    structlog.contextvars.bind_contextvars(
        org_id=str(principal.organization_id), role=principal.role.name
    )
    # The access-log middleware runs outside this task's context, so it reads these instead.
    request.state.org_id = str(principal.organization_id)
    request.state.role = principal.role.name
    return principal


def require_role(minimum: Role) -> Callable[[Principal], Awaitable[Principal]]:
    """Dependency factory: the caller's role must be at least ``minimum`` (else 403)."""

    async def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.allows(minimum):
            raise HTTPException(status_code=403, detail=f"Requires role {minimum.name.lower()}.")
        return principal

    return dependency


# Module-level so routes can use them as defaults (a call in a default argument is flagged by B008).
ANALYST_REQUIRED = Depends(require_role(Role.ANALYST))
VIEWER_REQUIRED = Depends(require_role(Role.VIEWER))


def tenant_of(principal: Principal) -> TenantContext:
    return TenantContext(principal.organization_id)


async def get_scope(
    request: Request, principal: Principal = VIEWER_REQUIRED
) -> AsyncIterator[Scope]:
    engine = request.app.state.engine
    if engine is None:
        raise HTTPException(status_code=503, detail="Database not configured.")
    settings: Settings = request.app.state.settings
    tenant = tenant_of(principal)
    async with tenant_session(engine, tenant, settings.db_app_role) as session:
        org = tenant.organization_id
        yield Scope(
            org,
            SqlPerformanceReader(session, org),
            SqlForecastStore(session, org),
            SqlAuditStore(session, org),
        )
