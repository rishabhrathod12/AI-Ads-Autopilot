"""Storage port for recommendations, approvals, execution attempts and the audit trail.

One store instance is bound to one organization (and one transaction), like the other stores.
Approval records, execution attempts and audit entries are append-only: no update, no delete.
"""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.domain.recommendations import (
    ApprovalRecord,
    AuditEntry,
    ExecutionAttempt,
    Recommendation,
    Status,
)


class RecommendationStore(Protocol):
    async def add_new(self, recs: Sequence[Recommendation]) -> list[Recommendation]:
        """Insert recommendations not already stored (same organization and content hash).

        Returns only those actually inserted, so generating twice creates no duplicates.
        """
        ...

    async def get(self, rec_id: UUID, *, lock: bool = False) -> Recommendation | None:
        """The recommendation, or None. ``lock`` takes a row lock until the transaction ends."""
        ...

    async def list_recommendations(
        self, status: Status | None = None, *, limit: int = 100
    ) -> list[Recommendation]: ...

    async def transition(self, rec_id: UUID, *, expected: Status, new: Status) -> bool:
        """Atomically change status only if it is currently ``expected``. False if it was not."""
        ...

    async def add_approval(self, record: ApprovalRecord) -> None:
        """Insert the decision. Raises if this recommendation already has one."""
        ...

    async def approval_for(self, rec_id: UUID) -> ApprovalRecord | None: ...

    async def add_execution_attempt(self, attempt: ExecutionAttempt) -> None: ...

    async def executions(self, *, limit: int = 100) -> list[ExecutionAttempt]: ...

    async def add_audit(self, entry: AuditEntry) -> None: ...

    async def audit_log(self, *, limit: int = 100) -> list[AuditEntry]: ...
