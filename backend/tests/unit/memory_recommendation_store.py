"""In-memory RecommendationStore for unit tests; mirrors the contract the SQL store must meet."""

import dataclasses
from collections.abc import Sequence
from uuid import UUID

from app.domain.recommendations import (
    ApprovalRecord,
    AuditEntry,
    ExecutionAttempt,
    Recommendation,
    Status,
)


class DuplicateApprovalError(Exception):
    pass


class MemoryRecommendationStore:
    def __init__(self, organization_id: UUID) -> None:
        self.org = organization_id
        self.recs: dict[UUID, Recommendation] = {}
        self.approvals: dict[UUID, ApprovalRecord] = {}  # keyed by recommendation id
        self.attempts: list[ExecutionAttempt] = []
        self.audit: list[AuditEntry] = []

    async def add_new(self, recs: Sequence[Recommendation]) -> list[Recommendation]:
        hashes = {r.content_hash for r in self.recs.values()}
        created: list[Recommendation] = []
        for r in recs:
            if r.organization_id != self.org:
                raise ValueError("recommendation belongs to another organization")
            if r.content_hash in hashes or r.id in self.recs:
                continue
            self.recs[r.id] = r
            hashes.add(r.content_hash)
            created.append(r)
        return created

    async def get(self, rec_id: UUID, *, lock: bool = False) -> Recommendation | None:
        return self.recs.get(rec_id)

    async def list_recommendations(
        self, status: Status | None = None, *, limit: int = 100
    ) -> list[Recommendation]:
        rows = [r for r in self.recs.values() if status is None or r.status is status]
        return sorted(rows, key=lambda r: (r.created_at, str(r.id)))[:limit]

    async def transition(self, rec_id: UUID, *, expected: Status, new: Status) -> bool:
        rec = self.recs.get(rec_id)
        if rec is None or rec.status is not expected:
            return False
        self.recs[rec_id] = dataclasses.replace(rec, status=new)
        return True

    async def add_approval(self, record: ApprovalRecord) -> None:
        if record.recommendation_id in self.approvals:
            raise DuplicateApprovalError
        self.approvals[record.recommendation_id] = record

    async def approval_for(self, rec_id: UUID) -> ApprovalRecord | None:
        return self.approvals.get(rec_id)

    async def add_execution_attempt(self, attempt: ExecutionAttempt) -> None:
        self.attempts.append(attempt)

    async def executions(self, *, limit: int = 100) -> list[ExecutionAttempt]:
        return list(reversed(self.attempts))[:limit]

    async def add_audit(self, entry: AuditEntry) -> None:
        self.audit.append(entry)

    async def audit_log(self, *, limit: int = 100) -> list[AuditEntry]:
        return list(reversed(self.audit))[:limit]
