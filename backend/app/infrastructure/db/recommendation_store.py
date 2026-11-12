"""SQL RecommendationStore bound to one tenant-scoped session (one transaction).

UNVERIFIED against a real database in the environment where this was written; the contract tests in
``tests/integration/test_recommendation_store.py`` must be run with TEST_DATABASE_URL set.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.recommendations import (
    ApprovalRecord,
    AuditEntry,
    Decision,
    ExecutionAttempt,
    Recommendation,
    RecType,
    Status,
)
from app.infrastructure.db.models import AuditLog
from app.infrastructure.db.recommendation_models import (
    ApprovalRow,
    ExecutionAttemptRow,
    RecommendationRow,
)


def _rec(r: RecommendationRow) -> Recommendation:
    return Recommendation(
        id=r.id,
        organization_id=r.organization_id,
        type=RecType(r.type),
        entity_type=r.entity_type,
        entity_id=r.entity_id,
        entity_label=r.entity_label,
        current_value=r.current_value,
        proposed_value=r.proposed_value,
        proposed_text=r.proposed_text,
        rationale=r.rationale,
        evidence=r.evidence,
        confidence=r.confidence,
        expected_impact=r.expected_impact,
        data_source=r.data_source,
        created_at=r.created_at,
        expires_at=r.expires_at,
        status=Status(r.status),
        content_hash=r.content_hash,  # as stored: approval compares it with a recomputation
    )


class SqlRecommendationStore:
    def __init__(self, session: AsyncSession, organization_id: UUID) -> None:
        self._s = session
        self._org = organization_id

    async def add_new(self, recs: Sequence[Recommendation]) -> list[Recommendation]:
        created: list[Recommendation] = []
        for r in recs:
            if r.organization_id != self._org:
                raise ValueError("recommendation belongs to another organization")
            stmt = (
                insert(RecommendationRow)
                .values(
                    id=r.id,
                    organization_id=r.organization_id,
                    type=r.type.value,
                    entity_type=r.entity_type,
                    entity_id=r.entity_id,
                    entity_label=r.entity_label,
                    current_value=r.current_value,
                    proposed_value=r.proposed_value,
                    proposed_text=r.proposed_text,
                    rationale=r.rationale,
                    evidence=r.evidence,
                    confidence=r.confidence,
                    expected_impact=r.expected_impact,
                    data_source=r.data_source,
                    content_hash=r.content_hash,
                    status=r.status.value,
                    created_at=r.created_at,
                    expires_at=r.expires_at,
                )
                .on_conflict_do_nothing()  # same org + content hash (or id): already stored
                .returning(RecommendationRow.id)
            )
            if (await self._s.execute(stmt)).scalar_one_or_none() is not None:
                created.append(r)
        return created

    async def get(self, rec_id: UUID, *, lock: bool = False) -> Recommendation | None:
        stmt = select(RecommendationRow).where(RecommendationRow.id == rec_id)
        if lock:
            stmt = stmt.with_for_update()
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _rec(row) if row is not None else None

    async def list_recommendations(
        self, status: Status | None = None, *, limit: int = 100
    ) -> list[Recommendation]:
        stmt = select(RecommendationRow)
        if status is not None:
            stmt = stmt.where(RecommendationRow.status == status.value)
        stmt = stmt.order_by(RecommendationRow.created_at, RecommendationRow.id).limit(limit)
        return [_rec(r) for r in (await self._s.execute(stmt)).scalars()]

    async def transition(self, rec_id: UUID, *, expected: Status, new: Status) -> bool:
        result = await self._s.execute(
            update(RecommendationRow)
            .where(RecommendationRow.id == rec_id, RecommendationRow.status == expected.value)
            .values(status=new.value)
        )
        return result.rowcount == 1  # type: ignore[attr-defined,no-any-return]

    async def add_approval(self, record: ApprovalRecord) -> None:
        self._s.add(
            ApprovalRow(
                id=record.id,
                organization_id=record.organization_id,
                recommendation_id=record.recommendation_id,
                decided_by=record.decided_by,
                decision=record.decision.value,
                comment=record.comment,
                content_hash=record.content_hash,
                decided_at=record.decided_at,
            )
        )
        await self._s.flush()  # the UNIQUE(recommendation_id) violation surfaces here

    async def approval_for(self, rec_id: UUID) -> ApprovalRecord | None:
        row = (
            await self._s.execute(
                select(ApprovalRow).where(ApprovalRow.recommendation_id == rec_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return ApprovalRecord(
            id=row.id,
            organization_id=row.organization_id,
            recommendation_id=row.recommendation_id,
            decided_by=row.decided_by,
            decision=Decision(row.decision),
            comment=row.comment,
            content_hash=row.content_hash,
            decided_at=row.decided_at,
        )

    async def add_execution_attempt(self, attempt: ExecutionAttempt) -> None:
        self._s.add(
            ExecutionAttemptRow(
                id=attempt.id,
                organization_id=attempt.organization_id,
                recommendation_id=attempt.recommendation_id,
                approval_id=attempt.approval_id,
                requested_by=attempt.requested_by,
                status=attempt.status,
                reason=attempt.reason,
                dry_run=attempt.dry_run,
                created_at=attempt.created_at,
            )
        )
        await self._s.flush()

    async def executions(self, *, limit: int = 100) -> list[ExecutionAttempt]:
        rows = (
            await self._s.execute(
                select(ExecutionAttemptRow)
                .order_by(ExecutionAttemptRow.created_at.desc(), ExecutionAttemptRow.id)
                .limit(limit)
            )
        ).scalars()
        return [
            ExecutionAttempt(
                id=r.id,
                organization_id=r.organization_id,
                recommendation_id=r.recommendation_id,
                approval_id=r.approval_id,
                requested_by=r.requested_by,
                status=r.status,
                reason=r.reason,
                dry_run=r.dry_run,
                created_at=r.created_at,
            )
            for r in rows
        ]

    async def add_audit(self, entry: AuditEntry) -> None:
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

    async def audit_log(self, *, limit: int = 100) -> list[AuditEntry]:
        rows = (
            await self._s.execute(
                select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id).limit(limit)
            )
        ).scalars()
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
            for r in rows
        ]
