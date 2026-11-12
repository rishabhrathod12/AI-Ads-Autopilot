"""Membership lookup in Postgres, run inside the tenant's own RLS scope."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.auth import Role
from app.domain.tenant import TenantContext
from app.infrastructure.db.models import Membership, User
from app.infrastructure.db.session import tenant_session


class SqlMembershipDirectory:
    """Role of a user in one organization.

    The query runs with that organization as the RLS tenant, so memberships of other organizations
    are invisible to it regardless of the WHERE clause.
    """

    def __init__(self, engine: AsyncEngine, app_role: str | None) -> None:
        self._engine = engine
        self._app_role = app_role

    async def role_for(self, subject: str, organization_id: UUID) -> Role | None:
        async with tenant_session(
            self._engine, TenantContext(organization_id), self._app_role
        ) as session:
            raw = await session.scalar(
                select(Membership.role)
                .join(User, User.id == Membership.user_id)
                .where(
                    User.auth_subject == subject,
                    Membership.organization_id == organization_id,
                )
            )
        return Role.parse(raw) if raw is not None else None
