"""Recommendations, human approval, the (blocked) execution gate, and the audit trail.

Roles: viewers read; analysts generate proposals; only admins and owners decide or ask to execute.
Nothing here changes advertising data. Errors that follow writes (a recorded expiry, a blocked
execution attempt) are *returned* as problem responses, not raised: an exception would roll the
transaction back and lose the very record that explains the refusal.
"""

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.errors import problem
from app.api.serialize import MOCK_NOTICE
from app.api.tenancy import current_principal, require_role, tenant_of
from app.application.analytics.dashboard import NoDataError
from app.application.analytics.thresholds import DEFAULT_TARGET_ACOS_PCT, DEFAULT_WINDOW_DAYS
from app.application.jsonable import to_jsonable
from app.application.optimization.generate import generate_recommendations
from app.application.ports.performance_reader import PerformanceReader
from app.application.ports.recommendation_store import RecommendationStore
from app.application.recommendations.service import Actor, RecommendationService
from app.config import Settings
from app.domain.auth import Principal, Role
from app.domain.kpi.periods import DateRange
from app.domain.recommendations import (
    ApprovalRecord,
    AuditEntry,
    Decision,
    ExecutionAttempt,
    ExpiredError,
    HashMismatchError,
    InvalidTransitionError,
    NotFoundError,
    Recommendation,
    RecommendationError,
    Status,
)
from app.infrastructure.db.performance_reader import SqlPerformanceReader
from app.infrastructure.db.recommendation_store import SqlRecommendationStore
from app.infrastructure.db.session import tenant_session

router = APIRouter(prefix="/api/v1", tags=["recommendations"])

_HASH = re.compile(r"[0-9a-f]{64}")
ANALYST = Depends(require_role(Role.ANALYST))
VIEWER = Depends(require_role(Role.VIEWER))
ADMIN = Depends(require_role(Role.ADMIN))


@dataclass(frozen=True)
class RecoDeps:
    organization_id: UUID
    reader: PerformanceReader
    store: RecommendationStore
    service: RecommendationService


async def get_reco_deps(
    request: Request, principal: Principal = Depends(current_principal)
) -> AsyncIterator[RecoDeps]:
    engine = request.app.state.engine
    if engine is None:
        raise HTTPException(503, "Database not configured.")
    settings: Settings = request.app.state.settings
    tenant = tenant_of(principal)
    org = tenant.organization_id
    async with tenant_session(engine, tenant, settings.db_app_role) as session:
        store = SqlRecommendationStore(session, org)
        yield RecoDeps(
            org, SqlPerformanceReader(session, org), store, RecommendationService(store, org)
        )


def _recommendation(r: Recommendation) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "type": r.type.value,
        "status": r.status.value,
        "entity": {"type": r.entity_type, "id": r.entity_id, "label": r.entity_label},
        "current_value_paise": r.current_value,
        "proposed_value_paise": r.proposed_value,
        "proposed_text": r.proposed_text,
        "rationale": r.rationale,
        "evidence": r.evidence,
        "confidence": r.confidence,
        "expected_impact": r.expected_impact,
        "data_source": r.data_source,
        "synthetic": r.data_source == "MOCK",
        "content_hash": r.content_hash,  # what an approver must echo back
        "created_at": to_jsonable(r.created_at),
        "expires_at": to_jsonable(r.expires_at),
    }


def _approval(a: ApprovalRecord) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "decision": a.decision.value,
        "decided_by": a.decided_by,
        "decided_at": to_jsonable(a.decided_at),
        "comment": a.comment,
        "content_hash": a.content_hash,
    }


def _attempt(a: ExecutionAttempt) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "recommendation_id": str(a.recommendation_id),
        "approval_id": str(a.approval_id) if a.approval_id else None,
        "requested_by": a.requested_by,
        "status": a.status,
        "reason": a.reason,
        "dry_run": a.dry_run,
        "created_at": to_jsonable(a.created_at),
    }


def _audit(e: AuditEntry) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "actor": e.actor,
        "action": e.action,
        "entity": {"type": e.entity_type, "id": e.entity_id},
        "before": e.before,
        "after": e.after,
        "request_id": e.request_id,
        "created_at": to_jsonable(e.created_at),
    }


def _notice(items: list[Recommendation]) -> dict[str, Any]:
    synthetic = any(r.data_source == "MOCK" for r in items)
    return {"synthetic": synthetic, "notice": MOCK_NOTICE if synthetic else None}


def _actor(request: Request, principal: Principal) -> Actor:
    return Actor(principal.subject, getattr(request.state, "request_id", None))


def _refuse(request: Request, exc: RecommendationError) -> Response:
    status = 404 if isinstance(exc, NotFoundError) else 409
    title = {
        NotFoundError: "Recommendation not found",
        InvalidTransitionError: "Recommendation is not pending",
        ExpiredError: "Recommendation has expired",
        HashMismatchError: "Content hash does not match",
    }.get(type(exc), "Conflict")
    return problem(request, status, title, str(exc))


def _now() -> datetime:
    return datetime.now(UTC)


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # The hash of the content the human reviewed; the approval is bound to it.
    content_hash: str = Field(min_length=64, max_length=64)
    comment: str | None = Field(None, max_length=1000)


@router.post("/recommendations/generate", status_code=201, response_model=None)
async def generate(
    request: Request,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    principal: Principal = ANALYST,
    deps: RecoDeps = Depends(get_reco_deps),
) -> dict[str, Any] | Response:
    """Compute proposals from the analytics and store the new ones as pending (idempotent)."""
    fresh = await deps.reader.freshness()
    if fresh.latest_date is None:
        raise NoDataError("no advertising data")
    end = date_to or fresh.latest_date
    start = date_from or end - timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    if start > end or (end - start).days >= 366:
        return problem(request, 422, "Invalid period")
    now = _now()
    result = await generate_recommendations(
        deps.reader,
        deps.organization_id,
        DateRange(start, end),
        now,
        target_acos_pct=DEFAULT_TARGET_ACOS_PCT,
    )
    created = await deps.service.add_generated(
        result.recommendations, _actor(request, principal), now
    )
    return {
        "data": {
            "proposed": len(result.recommendations),
            "created": created,
            "already_stored": len(result.recommendations) - created,
            "dropped_by_guardrails": result.dropped_by_guardrails,
            "period": [result.period.start.isoformat(), result.period.end.isoformat()],
        },
        "meta": _notice(result.recommendations),
    }


@router.get("/recommendations")
async def list_recommendations(
    status: Status | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    _principal: Principal = VIEWER,
    deps: RecoDeps = Depends(get_reco_deps),
) -> dict[str, Any]:
    items = await deps.store.list_recommendations(status, limit=limit)
    return {"data": [_recommendation(r) for r in items], "meta": _notice(items)}


@router.get("/recommendations/{rec_id}", response_model=None)
async def get_recommendation(
    rec_id: UUID,
    request: Request,
    _principal: Principal = VIEWER,
    deps: RecoDeps = Depends(get_reco_deps),
) -> dict[str, Any] | Response:
    r = await deps.store.get(rec_id)
    if r is None:
        return _refuse(request, NotFoundError("recommendation not found"))
    approval = await deps.store.approval_for(rec_id)
    return {
        "data": {**_recommendation(r), "approval": _approval(approval) if approval else None},
        "meta": _notice([r]),
    }


async def _decide(
    decision: Decision,
    rec_id: UUID,
    body: DecisionBody,
    request: Request,
    principal: Principal,
    deps: RecoDeps,
) -> dict[str, Any] | Response:
    if not _HASH.fullmatch(body.content_hash):
        return problem(request, 422, "content_hash must be 64 lowercase hex characters")
    try:
        res = await deps.service.decide(
            rec_id,
            decision,
            _actor(request, principal),
            approved_hash=body.content_hash,
            comment=body.comment,
            now=_now(),
        )
    except RecommendationError as exc:
        return _refuse(request, exc)
    return {
        "data": {
            **_recommendation(res.recommendation),
            "approval": _approval(res.approval),
            "replayed": res.replayed,
        },
        "meta": _notice([res.recommendation]),
    }


@router.post("/recommendations/{rec_id}/approve", response_model=None)
async def approve(
    rec_id: UUID,
    body: DecisionBody,
    request: Request,
    principal: Principal = ADMIN,
    deps: RecoDeps = Depends(get_reco_deps),
) -> dict[str, Any] | Response:
    """Record approval. This does NOT execute anything."""
    return await _decide(Decision.APPROVE, rec_id, body, request, principal, deps)


@router.post("/recommendations/{rec_id}/reject", response_model=None)
async def reject(
    rec_id: UUID,
    body: DecisionBody,
    request: Request,
    principal: Principal = ADMIN,
    deps: RecoDeps = Depends(get_reco_deps),
) -> dict[str, Any] | Response:
    return await _decide(Decision.REJECT, rec_id, body, request, principal, deps)


@router.post("/recommendations/{rec_id}/execute", response_model=None)
async def execute(
    rec_id: UUID,
    request: Request,
    principal: Principal = ADMIN,
    deps: RecoDeps = Depends(get_reco_deps),
) -> Response:
    """Ask to execute. In this build the answer is always 409 `blocked`: nothing is ever sent.

    The attempt and its audit entry are stored either way; the reason says which gate stopped it.
    """
    settings: Settings = request.app.state.settings
    try:
        attempt = await deps.service.request_execution(
            rec_id,
            _actor(request, principal),
            execution_enabled=settings.ad_execution_enabled,
            now=_now(),
        )
    except RecommendationError as exc:
        return _refuse(request, exc)
    return problem(
        request,
        409,
        "Execution blocked",
        f"{attempt.reason} (attempt {attempt.id}). No change was sent to any advertising account.",
    )


@router.get("/executions")
async def list_executions(
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    _principal: Principal = ADMIN,
    deps: RecoDeps = Depends(get_reco_deps),
) -> dict[str, Any]:
    return {"data": [_attempt(a) for a in await deps.store.executions(limit=limit)]}


@router.get("/audit-logs")
async def audit_logs(
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    _principal: Principal = ADMIN,
    deps: RecoDeps = Depends(get_reco_deps),
) -> dict[str, Any]:
    return {"data": [_audit(e) for e in await deps.store.audit_log(limit=limit)]}
