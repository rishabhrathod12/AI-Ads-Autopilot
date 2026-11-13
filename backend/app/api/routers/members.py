"""Who am I, and who is on my team. Sign-in is by invitation (ADR 0026).

``GET /me`` needs only a verified token (no organization chosen yet) and lists the organizations
the caller belongs to; on the way it links an invited user's first login. Team routes need admin.
Nobody can grant a role above their own, and an email address is never written to the audit log
(the audit scrubber would replace it): the membership id identifies the change.
"""

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.errors import problem
from app.api.tenancy import (
    current_principal,
    link_invitation,
    membership_directory,
    require_role,
    tenant_of,
    verified_claims,
)
from app.application.audit import audit_entry
from app.application.ports.audit_store import AuditStore
from app.application.ports.auth import Claims
from app.application.ports.member_store import AlreadyMemberError, Member, MemberStore
from app.config import Settings
from app.domain.auth import Principal, Role
from app.infrastructure.db.audit_store import SqlAuditStore
from app.infrastructure.db.member_store import SqlMemberStore
from app.infrastructure.db.session import tenant_session

router = APIRouter(prefix="/api/v1", tags=["members"])

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
ADMIN = Depends(require_role(Role.ADMIN))


class Invite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    role: Literal["viewer", "analyst", "admin", "owner"] = "viewer"


@dataclass(frozen=True)
class MemberDeps:
    organization_id: UUID
    members: MemberStore
    audit: AuditStore


async def get_member_deps(
    request: Request, principal: Principal = Depends(current_principal)
) -> AsyncIterator[MemberDeps]:
    engine = request.app.state.engine
    if engine is None:
        raise HTTPException(503, "Database not configured.")
    settings: Settings = request.app.state.settings
    tenant = tenant_of(principal)
    org = tenant.organization_id
    async with tenant_session(engine, tenant, settings.db_app_role) as session:
        yield MemberDeps(org, SqlMemberStore(session, org), SqlAuditStore(session, org))


def _member(m: Member) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "email": m.email,
        "role": m.role.name.lower(),
        "status": "active" if m.linked else "invited",
        "created_at": m.created_at.isoformat(),
    }


@router.get("/me")
async def me(request: Request, claims: Claims = Depends(verified_claims)) -> dict[str, Any]:
    directory = membership_directory(request)
    await link_invitation(directory, claims)
    orgs = await directory.organizations_of(claims.subject)
    return {
        "data": {
            "subject": claims.subject,
            "email": claims.email,
            "organizations": [
                {"id": str(o.organization_id), "name": o.name, "role": o.role.name.lower()}
                for o in orgs
            ],
        }
    }


@router.get("/members")
async def list_members(
    _principal: Principal = ADMIN, deps: MemberDeps = Depends(get_member_deps)
) -> dict[str, Any]:
    return {"data": [_member(m) for m in await deps.members.list_members()]}


@router.post("/members", status_code=201, response_model=None)
async def invite(
    body: Invite,
    request: Request,
    principal: Principal = ADMIN,
    deps: MemberDeps = Depends(get_member_deps),
) -> Any:
    email = body.email.strip().lower()
    if not _EMAIL.fullmatch(email):
        raise HTTPException(422, "That does not look like an email address.")
    role = Role.parse(body.role)
    assert role is not None  # noqa: S101 - the model only allows defined roles
    if role > principal.role:
        raise HTTPException(403, "You cannot grant a role above your own.")
    try:
        member = await deps.members.invite(email, role)
    except AlreadyMemberError:
        return problem(request, 409, "Already a member", "That email is already on this team.")
    await deps.audit.add_audit(
        audit_entry(
            org=deps.organization_id,
            actor=principal.subject,
            action="member.invited",
            entity_type="membership",
            entity_id=str(member.id),
            now=datetime.now(UTC),
            request_id=getattr(request.state, "request_id", None),
            after={"role": role.name.lower()},
        )
    )
    return {"data": _member(member)}
