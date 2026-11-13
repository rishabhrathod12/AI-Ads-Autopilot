"""SQL team store: members of one organization, and invitations by email."""

import uuid
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.member_store import AlreadyMemberError, Member
from app.domain.auth import Role
from app.infrastructure.db.models import Membership, User


class SqlMemberStore:
    def __init__(self, session: AsyncSession, organization_id: UUID) -> None:
        self._s = session
        self._org = organization_id

    async def list_members(self) -> list[Member]:
        rows = (
            await self._s.execute(
                select(
                    Membership.id,
                    User.email,
                    Membership.role,
                    User.auth_subject,
                    Membership.created_at,
                )
                .join(User, User.id == Membership.user_id)
                .where(Membership.organization_id == self._org)
                .order_by(User.email)
            )
        ).all()
        members: list[Member] = []
        for mid, email, raw, subject, created in rows:
            role = Role.parse(raw)
            if role is not None:  # an unknown stored role value grants nothing, so it is hidden
                members.append(Member(mid, email, role, subject is not None, created))
        return members

    async def invite(self, email: str, role: Role) -> Member:
        email = email.strip().lower()
        user = (
            await self._s.execute(select(User).where(func.lower(User.email) == email))
        ).scalar_one_or_none()
        if user is None:
            user = User(id=uuid.uuid4(), email=email, auth_subject=None)
            self._s.add(user)
            await self._s.flush()
        existing = await self._s.scalar(
            select(Membership.id).where(
                Membership.user_id == user.id, Membership.organization_id == self._org
            )
        )
        if existing is not None:
            raise AlreadyMemberError(email)
        membership = Membership(
            id=uuid.uuid4(), user_id=user.id, organization_id=self._org, role=role.name.lower()
        )
        try:
            async with self._s.begin_nested():
                self._s.add(membership)
                await self._s.flush()
        except IntegrityError:  # a concurrent identical invite won the race
            raise AlreadyMemberError(email) from None
        await self._s.refresh(membership)
        return Member(
            membership.id, user.email, role, user.auth_subject is not None, membership.created_at
        )
