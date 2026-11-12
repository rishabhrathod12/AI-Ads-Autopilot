"""Append-only audit trail port. There is no update and no delete."""

from datetime import datetime
from typing import Protocol

from app.domain.audit import AuditEntry


class AuditStore(Protocol):
    async def add_audit(self, entry: AuditEntry) -> None: ...

    async def audit_log(
        self,
        *,
        limit: int = 100,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        before: datetime | None = None,
    ) -> list[AuditEntry]:
        """Newest first. ``before`` pages backwards: entries strictly older than this instant."""
        ...
