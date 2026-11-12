"""SQL audit store: INSERT and SELECT only (the app role has no UPDATE or DELETE on audit_logs).

Verified 2026-11-12 against PostgreSQL 18.6 with pgvector 0.8.7 (a throwaway local cluster).
Not yet run on PostgreSQL 16, the CI image.
Covered by tests/integration/test_audit_store.py and test_rls.py.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.audit import AuditEntry
from app.infrastructure.db.models import AuditLog


class SqlAuditStore:
    def __init__(self, session: AsyncSession, organization_id: UUID) -> None:
        self._s = session
        self._org = organization_id

    async def add_audit(self, entry: AuditEntry) -> None:
        if entry.organization_id != self._org:
            raise ValueError("audit entry belongs to another organization")
        self._s.add(
            AuditLog(
                id=entry.id,
                organization_id=entry.organization_id,
                actor=entry.actor,
                action=entry.action,
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                before=entry.before,
                after=entry.after,
                request_id=entry.request_id,
                created_at=entry.created_at,
            )
        )
        await self._s.flush()

    async def audit_log(
        self,
        *,
        limit: int = 100,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        before: datetime | None = None,
    ) -> list[AuditEntry]:
        stmt = select(AuditLog)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        if entity_type is not None:
            stmt = stmt.where(AuditLog.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(AuditLog.entity_id == entity_id)
        if before is not None:
            stmt = stmt.where(AuditLog.created_at < before)
        stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id).limit(limit)
        return [
            AuditEntry(
                id=r.id,
                organization_id=r.organization_id,
                actor=r.actor,
                action=r.action,
                entity_type=r.entity_type or "",
                entity_id=r.entity_id or "",
                before=r.before,
                after=r.after,
                request_id=r.request_id,
                created_at=r.created_at,
            )
            for r in (await self._s.execute(stmt)).scalars()
        ]
