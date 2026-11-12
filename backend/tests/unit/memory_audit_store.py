"""In-memory AuditStore for unit tests (same contract as the SQL store)."""

from datetime import datetime

from app.domain.audit import AuditEntry


class MemoryAuditStore:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    async def add_audit(self, entry: AuditEntry) -> None:
        self.entries.append(entry)

    async def audit_log(
        self,
        *,
        limit: int = 100,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        before: datetime | None = None,
    ) -> list[AuditEntry]:
        rows = [
            e
            for e in reversed(self.entries)
            if (action is None or e.action == action)
            and (entity_type is None or e.entity_type == entity_type)
            and (entity_id is None or e.entity_id == entity_id)
            and (before is None or e.created_at < before)
        ]
        return rows[:limit]
