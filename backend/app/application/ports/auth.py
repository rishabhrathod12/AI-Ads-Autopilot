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
    email: str | None = None  # only trusted for linking when ``email_verified`` is True
    email_verified: bool = False


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> Claims:
        """Return the verified claims or raise ``AuthError``."""
        ...


class MembershipDirectory(Protocol):
    async def role_for(self, subject: str, organization_id: UUID) -> Role | None:
        """The subject's role in that organization, or None if they are not a member."""
        ...

    async def claim_invitation(self, subject: str, email: str) -> bool:
        """Link ``subject`` to the invited user with this (provider-verified) email.

        Only an invited user whose ``auth_subject`` is still empty can be linked, and a subject
        already linked to someone is never linked twice. Returns whether a link was made.
        """
        ...

    async def organizations_of(self, subject: str) -> list["OrganizationAccess"]:
        """Every organization the subject belongs to, with their role in each."""
        ...


@dataclass(frozen=True)
class OrganizationAccess:
    organization_id: UUID
    name: str
    role: Role
