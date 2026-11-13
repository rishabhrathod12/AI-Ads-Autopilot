"""Port for an organization's team: who is a member, and inviting someone by email."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.auth import Role


class AlreadyMemberError(Exception):
    """That email already belongs to this organization."""


@dataclass(frozen=True)
class Member:
    id: UUID  # the membership id
    email: str
    role: Role
    linked: bool  # False while the invitation has not been used by a first login
    created_at: datetime


class MemberStore(Protocol):
    async def list_members(self) -> list[Member]: ...

    async def invite(self, email: str, role: Role) -> Member:
        """Add the email to this organization with ``role``. Raises ``AlreadyMemberError``."""
        ...
