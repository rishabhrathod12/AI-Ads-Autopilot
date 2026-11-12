"""Recommendations and their approval lifecycle. Pure rules, no I/O (ADR 0022).

A recommendation is a *proposal* computed by deterministic code. It changes nothing by itself:
a human with the admin role must approve it, bound to its content hash, before it could ever be
executed, and execution is separately gated and disabled in this build.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

# Guardrails, in one place. A recommendation that breaks one is never created.
MAX_BID_CHANGE_PCT = 20
MAX_BUDGET_CHANGE_PCT = 20
MIN_BID_PAISE = 1
RECOMMENDATION_TTL = timedelta(days=7)  # a pending recommendation expires after this
APPROVAL_TTL = timedelta(hours=24)  # an approval is only good for execution this long


class RecType(StrEnum):
    BID_CHANGE = "bid_change"
    BUDGET_CHANGE = "budget_change"
    NEGATIVE_KEYWORD = "negative_keyword"


class Status(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class RecommendationError(Exception):
    """Base for rule violations; each maps to a distinct HTTP status in the API layer."""


class NotFoundError(RecommendationError):
    pass


class InvalidTransitionError(RecommendationError):
    """The recommendation is not pending (already decided, or expired)."""


class ExpiredError(RecommendationError):
    pass


class HashMismatchError(RecommendationError):
    """The caller approved different content than what is stored."""


class GuardrailError(RecommendationError):
    pass


@dataclass(frozen=True)
class Recommendation:
    id: UUID
    organization_id: UUID
    type: RecType
    entity_type: str  # "target" | "campaign" | "search_term"
    entity_id: str
    entity_label: str
    current_value: int | None  # paise, for bid / budget changes
    proposed_value: int | None  # paise, for bid / budget changes
    proposed_text: str | None  # the negative keyword text, for negative_keyword
    rationale: str
    evidence: dict[str, Any]  # the numbers the proposal was computed from (traceability)
    confidence: str
    expected_impact: str
    data_source: str  # "MOCK" until real data exists
    created_at: datetime
    expires_at: datetime
    status: Status = Status.PENDING
    content_hash: str = field(default="")

    def __post_init__(self) -> None:
        if not self.content_hash:
            object.__setattr__(self, "content_hash", content_hash(self))


def content_hash(rec: Recommendation) -> str:
    """SHA-256 of the fields that define WHAT is being proposed (not id, status or timestamps).

    Approval is bound to this hash, so changing any of these after approval invalidates it.
    """
    canonical = {
        "organization_id": str(rec.organization_id),
        "type": rec.type.value,
        "entity_type": rec.entity_type,
        "entity_id": rec.entity_id,
        "current_value": rec.current_value,
        "proposed_value": rec.proposed_value,
        "proposed_text": rec.proposed_text,
        "evidence": rec.evidence,
        "data_source": rec.data_source,
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def guardrail_violations(rec: Recommendation) -> list[str]:
    """Empty when the proposal is within the business limits."""
    problems: list[str] = []
    if rec.type in (RecType.BID_CHANGE, RecType.BUDGET_CHANGE):
        cur, new = rec.current_value, rec.proposed_value
        if cur is None or new is None or cur <= 0 or new < MIN_BID_PAISE:
            return ["current and proposed values must be positive amounts"]
        limit = MAX_BID_CHANGE_PCT if rec.type is RecType.BID_CHANGE else MAX_BUDGET_CHANGE_PCT
        if abs(new - cur) * 100 > cur * limit:
            problems.append(f"change of {(new - cur) * 100 / cur:+.1f}% exceeds the {limit}% cap")
        if new == cur:
            problems.append("proposed value equals the current value")
    elif not rec.proposed_text or rec.proposed_value is not None:
        problems.append("a negative keyword needs text and no numeric value")
    if rec.expires_at <= rec.created_at:
        problems.append("expiry must be after creation")
    if rec.data_source not in ("MOCK", "AMAZON_ADS_API"):
        problems.append("unknown data source")
    return problems


def is_expired(rec: Recommendation, now: datetime) -> bool:
    return now >= rec.expires_at


def next_status(rec: Recommendation, decision: Decision, now: datetime) -> Status:
    """The status a decision leads to, or an error. The only place the lifecycle is defined."""
    if rec.status is not Status.PENDING:
        raise InvalidTransitionError(f"recommendation is {rec.status.value}, not pending")
    if is_expired(rec, now):
        raise ExpiredError("recommendation has expired")
    return Status.APPROVED if decision is Decision.APPROVE else Status.REJECTED


@dataclass(frozen=True)
class ApprovalRecord:
    """Immutable: one per recommendation, written with the status change."""

    id: UUID
    organization_id: UUID
    recommendation_id: UUID
    decided_by: str
    decision: Decision
    comment: str | None
    content_hash: str  # the hash the human approved
    decided_at: datetime


@dataclass(frozen=True)
class ExecutionAttempt:
    id: UUID
    organization_id: UUID
    recommendation_id: UUID
    approval_id: UUID | None
    requested_by: str
    status: str  # "blocked" in this build; nothing is ever sent to an ads provider
    reason: str
    dry_run: bool
    created_at: datetime


@dataclass(frozen=True)
class AuditEntry:
    id: UUID
    organization_id: UUID
    actor: str
    action: str
    entity_type: str
    entity_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    request_id: str | None
    created_at: datetime
