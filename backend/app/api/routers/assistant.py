"""Assistant API: ask questions about the account, read back stored answers and their evidence.

The assistant is read-only. Nothing here changes advertising data, and no endpoint accepts a
tenant id: the tenant comes from the same (dev-only) resolution as every other route.
"""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.ratelimit import RateLimiter
from app.api.tenancy import resolve_tenant
from app.application.assistant.service import (
    MAX_QUESTION_CHARS,
    AssistantService,
    InvalidQuestionError,
    ThreadNotFoundError,
)
from app.application.assistant.tools import ToolContext
from app.application.ports.assistant_store import AssistantStore, StoredMessage, StoredRun
from app.application.ports.llm import LLMError, LLMNotConfiguredError
from app.config import Settings
from app.domain.tenant import TenantContext
from app.infrastructure.db.assistant_store import SqlAssistantStore, SqlSpendLedger
from app.infrastructure.db.forecast_store import SqlForecastStore
from app.infrastructure.db.knowledge_store import SqlKnowledgeStore
from app.infrastructure.db.performance_reader import SqlPerformanceReader
from app.infrastructure.db.session import tenant_session
from app.infrastructure.llm.factory import build_budgeted_client, build_embedder, pricing_of

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])


@dataclass
class AssistantDeps:
    """Everything one request needs. ``service`` is built lazily so reads work unconfigured."""

    organization_id: UUID
    store: AssistantStore
    make_service: Callable[[], AssistantService]


class NewThread(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field("Conversation", max_length=200)


class NewMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


async def get_assistant(
    request: Request, tenant: TenantContext = Depends(resolve_tenant)
) -> AsyncIterator[AssistantDeps]:
    engine = request.app.state.engine
    if engine is None:
        raise HTTPException(503, "Database not configured.")
    settings: Settings = request.app.state.settings
    org = tenant.organization_id
    async with tenant_session(engine, tenant, settings.db_app_role) as session:
        store = SqlAssistantStore(session, org)

        def make_service() -> AssistantService:
            ledger = SqlSpendLedger(session, org)
            try:
                llm = build_budgeted_client(settings, ledger, org)
            except LLMNotConfiguredError:
                raise HTTPException(
                    503, "The assistant is not configured (set LLM_PROVIDER and its keys)."
                ) from None
            knowledge: SqlKnowledgeStore | None = None
            embedder = None
            if settings.embedding_provider != "none":
                try:
                    embedder = build_embedder(settings, ledger, org)
                    knowledge = SqlKnowledgeStore(session, org)
                except LLMNotConfiguredError:
                    embedder = None  # answer from account data only
            ctx = ToolContext(
                SqlPerformanceReader(session, org),
                SqlForecastStore(session, org),
                knowledge,
                embedder,
            )
            return AssistantService(llm, ctx, store, pricing_of(settings))

        yield AssistantDeps(org, store, make_service)


def limiter_of(request: Request) -> RateLimiter:
    limiter: RateLimiter | None = getattr(request.app.state, "assistant_limiter", None)
    if limiter is None:
        settings: Settings = request.app.state.settings
        limiter = RateLimiter(settings.assistant_questions_per_minute)
        request.app.state.assistant_limiter = limiter
    return limiter


# ---- views ------------------------------------------------------------------------------------
def data_used(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The 'data used' chips: what each cited tool result was, without its (large) payload."""
    return [
        {
            "evidence_id": e["id"],
            "tool": e["tool"],
            "arguments": e["arguments"],
            "data_sources": e["data_sources"],
            "synthetic": e["synthetic"],
            "as_of": e["as_of"],
            "period": e["period"],
            "truncated": e["truncated"],
            "flags": e["flags"],
        }
        for e in evidence
    ]


def run_view(run: StoredRun, *, full: bool = False) -> dict[str, Any]:
    view: dict[str, Any] = {
        "run_id": str(run.id),
        "thread_id": str(run.thread_id),
        "status": run.status,
        "question": run.question,
        "answer": run.answer,
        "data_used": data_used(run.evidence),
        "flags": run.flags,
        "model": run.model,
        "usage": {
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "cost_usd": str(run.cost_usd),
            "llm_calls": run.llm_calls,
            "tool_calls": run.tool_calls,
            "validation_attempts": run.validation_attempts,
        },
        "created_at": run.created_at.isoformat(),
    }
    if full:  # the audit view: every tool result, the step trace, why drafts were rejected
        view["evidence"] = run.evidence
        view["trace"] = run.trace
        view["violations"] = run.violations
    return view


def message_view(m: StoredMessage) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "role": m.role,
        "content": m.content,
        "run_id": str(m.run_id) if m.run_id else None,
        "created_at": m.created_at.isoformat(),
    }


# ---- routes -----------------------------------------------------------------------------------
@router.post("/threads", status_code=201)
async def create_thread(
    body: NewThread | None = None, deps: AssistantDeps = Depends(get_assistant)
) -> dict[str, Any]:
    title = (body or NewThread()).title
    return {"thread_id": str(await deps.make_service().start_thread(title))}


@router.get("/threads/{thread_id}")
async def get_thread(
    thread_id: UUID, deps: AssistantDeps = Depends(get_assistant)
) -> dict[str, Any]:
    if not await deps.store.thread_exists(thread_id):
        raise HTTPException(404, "No such conversation.")
    runs = await deps.store.runs(thread_id, 20)
    return {
        "thread_id": str(thread_id),
        "messages": [message_view(m) for m in await deps.store.messages(thread_id, 50)],
        "runs": [run_view(r) for r in runs],
    }


@router.post("/threads/{thread_id}/messages")
async def ask(
    thread_id: UUID,
    body: NewMessage,
    request: Request,
    deps: AssistantDeps = Depends(get_assistant),
) -> dict[str, Any]:
    if not limiter_of(request).allow(deps.organization_id):
        raise HTTPException(429, "Too many questions. Wait a moment and try again.")
    service = deps.make_service()
    try:
        _, run_id = await service.ask(thread_id, body.question)
    except InvalidQuestionError as exc:
        raise HTTPException(422, str(exc)) from None
    except ThreadNotFoundError:
        raise HTTPException(404, "No such conversation.") from None
    except LLMError:  # the loop handles provider failures itself; this is a last line of defence
        raise HTTPException(502, "The assistant could not complete this request.") from None
    stored = await deps.store.get_run(run_id)
    if stored is None:  # cannot happen: saved in this transaction
        raise HTTPException(500, "The answer was not saved.")
    return run_view(stored)


@router.get("/runs/{run_id}")
async def get_run(run_id: UUID, deps: AssistantDeps = Depends(get_assistant)) -> dict[str, Any]:
    stored = await deps.store.get_run(run_id)
    if stored is None:
        raise HTTPException(404, "No such answer.")
    return run_view(stored, full=True)
