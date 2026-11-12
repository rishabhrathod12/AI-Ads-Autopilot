"""Approval workflow: decide on recommendations, and the (blocked) execution gate.

Rules enforced here, in order, on every decision:
  1. the recommendation exists in this organization (the store is tenant-bound);
  2. it is still pending and has not expired (expiry is recorded, not just reported);
  3. the caller approved the hash of the content that is actually stored;
  4. the decision, the status change and the audit entry are written together, in one transaction.
Deciding twice is idempotent only for the identical decision by the same person; anything else is
a conflict. Nothing in this module ever calls an advertising provider.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.application.ports.recommendation_store import RecommendationStore
from app.domain.recommendations import (
    APPROVAL_TTL,
    ApprovalRecord,
    AuditEntry,
    Decision,
    ExecutionAttempt,
    ExpiredError,
    HashMismatchError,
    InvalidTransitionError,
    NotFoundError,
    Recommendation,
    Status,
    content_hash,
    is_expired,
    next_status,
)


@dataclass(frozen=True)
class Actor:
    subject: str
    request_id: str | None = None


@dataclass(frozen=True)
class DecisionResult:
    recommendation: Recommendation
    approval: ApprovalRecord
    replayed: bool  # True when an identical earlier decision was returned instead of a new one


def _audit(
    org: UUID,
    actor: Actor,
    action: str,
    rec: Recommendation,
    now: datetime,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> AuditEntry:
    return AuditEntry(
        id=uuid.uuid4(),
        organization_id=org,
        actor=actor.subject,
        action=action,
        entity_type="recommendation",
        entity_id=str(rec.id),
        before=before,
        after=after,
        request_id=actor.request_id,
        created_at=now,
    )


class RecommendationService:
    def __init__(self, store: RecommendationStore, organization_id: UUID) -> None:
        self._store = store
        self._org = organization_id

    async def add_generated(self, recs: list[Recommendation], actor: Actor, now: datetime) -> int:
        created = await self._store.add_new(recs)
        for rec in created:
            await self._store.add_audit(
                _audit(
                    self._org,
                    actor,
                    "recommendation.created",
                    rec,
                    now,
                    None,
                    {"status": rec.status.value, "content_hash": rec.content_hash},
                )
            )
        return len(created)

    async def decide(
        self,
        rec_id: UUID,
        decision: Decision,
        actor: Actor,
        *,
        approved_hash: str,
        comment: str | None,
        now: datetime,
    ) -> DecisionResult:
        rec = await self._store.get(rec_id, lock=True)
        if rec is None:
            raise NotFoundError("recommendation not found")

        if rec.status is not Status.PENDING:
            earlier = await self._store.approval_for(rec_id)
            if (
                earlier is not None
                and earlier.decision is decision
                and earlier.decided_by == actor.subject
                and earlier.content_hash == approved_hash
            ):
                return DecisionResult(rec, earlier, replayed=True)
            raise InvalidTransitionError(f"recommendation is {rec.status.value}, not pending")

        if is_expired(rec, now):  # record the expiry; the audit trail shows it
            if await self._store.transition(rec_id, expected=Status.PENDING, new=Status.EXPIRED):
                await self._store.add_audit(
                    _audit(
                        self._org,
                        actor,
                        "recommendation.expired",
                        rec,
                        now,
                        {"status": "pending"},
                        {"status": "expired"},
                    )
                )
            raise ExpiredError("recommendation has expired")

        new_status = next_status(rec, decision, now)
        # Recompute from the stored content: the hash on the row could have been tampered with.
        if approved_hash != content_hash(rec) or approved_hash != rec.content_hash:
            raise HashMismatchError("the approved content hash does not match the stored content")

        if not await self._store.transition(rec_id, expected=Status.PENDING, new=new_status):
            raise InvalidTransitionError("recommendation is no longer pending")
        record = ApprovalRecord(
            id=uuid.uuid4(),
            organization_id=self._org,
            recommendation_id=rec_id,
            decided_by=actor.subject,
            decision=decision,
            comment=comment,
            content_hash=approved_hash,
            decided_at=now,
        )
        await self._store.add_approval(record)
        await self._store.add_audit(
            _audit(
                self._org,
                actor,
                f"recommendation.{new_status.value}",
                rec,
                now,
                {"status": Status.PENDING.value},
                {"status": new_status.value, "content_hash": approved_hash, "comment": comment},
            )
        )
        updated = await self._store.get(rec_id)
        assert updated is not None  # noqa: S101 - just transitioned inside this transaction
        return DecisionResult(updated, record, replayed=False)

    async def request_execution(
        self, rec_id: UUID, actor: Actor, *, execution_enabled: bool, now: datetime
    ) -> ExecutionAttempt:
        """Check every execution precondition, record the attempt, and (in this build) never send.

        The attempt is written, and audited, before anything else could happen. Every outcome
        today is ``blocked``: with execution disabled that is the policy, and with it enabled
        there is still no provider implementation (M9), so nothing is ever sent.
        """
        rec = await self._store.get(rec_id)
        if rec is None:
            raise NotFoundError("recommendation not found")
        approval = await self._store.approval_for(rec_id)
        reason = self._block_reason(rec, approval, execution_enabled, now)
        attempt = ExecutionAttempt(
            id=uuid.uuid4(),
            organization_id=self._org,
            recommendation_id=rec_id,
            approval_id=approval.id if approval else None,
            requested_by=actor.subject,
            status="blocked",
            reason=reason,
            dry_run=True,
            created_at=now,
        )
        await self._store.add_execution_attempt(attempt)
        await self._store.add_audit(
            _audit(
                self._org,
                actor,
                "execution.blocked",
                rec,
                now,
                None,
                {"reason": reason, "attempt_id": str(attempt.id)},
            )
        )
        return attempt

    @staticmethod
    def _block_reason(
        rec: Recommendation, approval: ApprovalRecord | None, enabled: bool, now: datetime
    ) -> str:
        if not enabled:
            return "execution_disabled"
        if rec.status is not Status.APPROVED or approval is None:
            return "not_approved"
        if approval.decision is not Decision.APPROVE:
            return "not_approved"
        if now >= approval.decided_at + APPROVAL_TTL:
            return "approval_expired"
        if approval.content_hash != content_hash(rec) or rec.content_hash != approval.content_hash:
            return "content_changed_since_approval"
        return "no_execution_provider"  # M9: nothing can be sent yet
