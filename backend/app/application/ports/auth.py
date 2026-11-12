"""Ports for authentication: verifying a bearer token and looking up organization membership."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.domain.auth import Role


class AuthError(Exception):
    """The credential is missing, malformed, expired or untrusted. Carries no detail on purpose."""


@dataclass(frozen=True)
class Claims:
    subject: str


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> Claims:
        """Return the verified claims or raise ``AuthError``."""
        ...


class MembershipDirectory(Protocol):
    async def role_for(self, subject: str, organization_id: UUID) -> Role | None:
        """The subject's role in that organization, or None if they are not a member."""
        ...
