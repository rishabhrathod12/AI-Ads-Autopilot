"""Async engine and tenant-scoped sessions (RLS via transaction-local settings)."""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.domain.tenant import TenantContext

_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def create_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


@asynccontextmanager
async def tenant_session(
    engine: AsyncEngine, ctx: TenantContext, app_role: str | None = None
) -> AsyncIterator[AsyncSession]:
    """Open a transaction whose RLS tenant is ``ctx``.

    ``set_config(..., is_local=true)`` scopes the tenant to this transaction only, so it cannot
    leak to another request that reuses the pooled connection.
    """
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        if app_role is not None:
            if not _ROLE_RE.match(app_role):
                raise ValueError("invalid database role name")
            await session.execute(text(f'SET LOCAL ROLE "{app_role}"'))
        await session.execute(
            text("SELECT set_config('app.org_id', :org, true)"),
            {"org": str(ctx.organization_id)},
        )
        yield session


@asynccontextmanager
async def subject_session(
    engine: AsyncEngine, subject: str, app_role: str | None = None
) -> AsyncIterator[AsyncSession]:
    """A transaction that can see only the caller's *own* memberships and their organizations.

    Used before an organization is chosen (listing "my organizations", linking an invitation). The
    subject is the verified token subject, set by the server after verification; it is never taken
    from a request field. No tenant is set, so no other organization's rows are visible.
    """
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        if app_role is not None:
            if not _ROLE_RE.match(app_role):
                raise ValueError("invalid database role name")
            await session.execute(text(f'SET LOCAL ROLE "{app_role}"'))
        await session.execute(
            text("SELECT set_config('app.subject', :subject, true)"), {"subject": subject}
        )
        yield session


@asynccontextmanager
async def maintenance_session(
    engine: AsyncEngine, app_role: str | None = None
) -> AsyncIterator[AsyncSession]:
    """A transaction that may list organizations; for the scheduled-job runner only (0008).

    Nothing else becomes visible: every other table keeps its tenant policy, and no tenant is set.
    Use it to read organization ids, then do the real work in ``tenant_session`` per organization.
    """
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        if app_role is not None:
            if not _ROLE_RE.match(app_role):
                raise ValueError("invalid database role name")
            await session.execute(text(f'SET LOCAL ROLE "{app_role}"'))
        await session.execute(text("SELECT set_config('app.maintenance', 'on', true)"))
        yield session
