"""In-memory MemberStore for unit tests (same contract as the SQL store)."""

import uuid
from datetime import UTC, datetime

from app.application.ports.member_store import AlreadyMemberError, Member
from app.domain.auth import Role


class MemoryMemberStore:
    def __init__(self) -> None:
        self.members: list[Member] = []

    async def list_members(self) -> list[Member]:
        return sorted(self.members, key=lambda m: m.email)

    async def invite(self, email: str, role: Role) -> Member:
        email = email.strip().lower()
        if any(m.email == email for m in self.members):
            raise AlreadyMemberError(email)
        member = Member(uuid.uuid4(), email, role, False, datetime.now(UTC))
        self.members.append(member)
        return member
