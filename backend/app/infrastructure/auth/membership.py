"""Membership lookup in Postgres, run inside the tenant's own RLS scope."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.ports.auth import OrganizationAccess
from app.domain.auth import Role
from app.domain.tenant import TenantContext
from app.infrastructure.db.models import Membership, Organization, User
from app.infrastructure.db.session import subject_session, tenant_session

_CLAIM = text(
    "UPDATE users SET auth_subject = :subject "
    "WHERE lower(email) = :email AND auth_subject IS NULL "
    "AND NOT EXISTS (SELECT 1 FROM users WHERE auth_subject = :subject)"
)


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

    async def claim_invitation(self, subject: str, email: str) -> bool:
        """Attach ``subject`` to the invited, still-unlinked user with this email."""
        async with subject_session(self._engine, subject, self._app_role) as session:
            result = await session.execute(_CLAIM, {"subject": subject, "email": email.lower()})
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def organizations_of(self, subject: str) -> list[OrganizationAccess]:
        async with subject_session(self._engine, subject, self._app_role) as session:
            rows = (
                await session.execute(
                    select(Organization.id, Organization.name, Membership.role)
                    .join(Membership, Membership.organization_id == Organization.id)
                    .join(User, User.id == Membership.user_id)
                    .where(User.auth_subject == subject)
                    .order_by(Organization.name, Organization.id)
                )
            ).all()
        out: list[OrganizationAccess] = []
        for org_id, name, raw in rows:
            role = Role.parse(raw)
            if role is not None:  # an unknown stored role value grants nothing
                out.append(OrganizationAccess(org_id, name, role))
        return out
