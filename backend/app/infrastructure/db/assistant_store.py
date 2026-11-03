"""SQL stores for assistant conversations and the LLM spend ledger (tenant-scoped session)."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.assistant.loop import AssistantRun
from app.application.assistant.records import (
    answer_record,
    evidence_records,
    trace_records,
    violation_records,
)
from app.application.ports.assistant_store import StoredMessage, StoredRun
from app.infrastructure.db import assistant_models as m


def _run(row: m.AssistantRun) -> StoredRun:
    return StoredRun(
        id=row.id,
        thread_id=row.thread_id,
        question=row.question,
        status=row.status,
        answer=row.answer,
        evidence=row.evidence,
        trace=row.trace,
        violations=row.violations,
        flags=row.flags,
        model=row.model,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cost_usd=row.cost_usd,
        llm_calls=row.llm_calls,
        tool_calls=row.tool_calls,
        validation_attempts=row.validation_attempts,
        created_at=row.created_at,
    )


class SqlAssistantStore:
    def __init__(self, session: AsyncSession, organization_id: UUID) -> None:
        self._s = session
        self._org = organization_id

    async def create_thread(self, title: str) -> UUID:
        thread_id = uuid4()
        self._s.add(m.AssistantThread(id=thread_id, organization_id=self._org, title=title[:200]))
        await self._s.flush()
        return thread_id

    async def thread_exists(self, thread_id: UUID) -> bool:
        found = await self._s.scalar(
            select(m.AssistantThread.id).where(
                m.AssistantThread.id == thread_id, m.AssistantThread.organization_id == self._org
            )
        )
        return found is not None

    async def messages(self, thread_id: UUID, limit: int = 20) -> list[StoredMessage]:
        rows = (
            await self._s.scalars(
                select(m.AssistantMessage)
                .where(
                    m.AssistantMessage.thread_id == thread_id,
                    m.AssistantMessage.organization_id == self._org,
                )
                .order_by(m.AssistantMessage.created_at.desc(), m.AssistantMessage.id)
                .limit(limit)
            )
        ).all()
        return [
            StoredMessage(r.id, r.role, r.content, r.run_id, r.created_at)  # type: ignore[arg-type]
            for r in reversed(rows)
        ]

    async def save_turn(self, thread_id: UUID, run: AssistantRun, cost_usd: Decimal) -> UUID:
        run_id = uuid4()
        self._s.add(
            m.AssistantRun(
                id=run_id,
                organization_id=self._org,
                thread_id=thread_id,
                question=run.question,
                status=run.status.value,
                answer=answer_record(run),
                evidence=evidence_records(run),
                trace=trace_records(run),
                violations=violation_records(run),
                flags=list(run.flags),
                model=run.model or "none",
                input_tokens=run.input_tokens,
                output_tokens=run.output_tokens,
                cost_usd=cost_usd,
                llm_calls=run.llm_calls,
                tool_calls=run.tool_calls,
                validation_attempts=run.validation_attempts,
            )
        )
        await self._s.flush()  # the run must exist before the messages that reference it
        self._s.add(
            m.AssistantMessage(
                id=uuid4(),
                organization_id=self._org,
                thread_id=thread_id,
                role="user",
                content=run.question,
            )
        )
        await self._s.flush()
        self._s.add(
            m.AssistantMessage(
                id=uuid4(),
                organization_id=self._org,
                thread_id=thread_id,
                role="assistant",
                content=run.answer_text,
                run_id=run_id,
            )
        )
        await self._s.flush()
        return run_id

    async def get_run(self, run_id: UUID) -> StoredRun | None:
        row = await self._s.scalar(
            select(m.AssistantRun).where(
                m.AssistantRun.id == run_id, m.AssistantRun.organization_id == self._org
            )
        )
        return _run(row) if row else None

    async def runs(self, thread_id: UUID, limit: int = 20) -> list[StoredRun]:
        rows = (
            await self._s.scalars(
                select(m.AssistantRun)
                .where(
                    m.AssistantRun.thread_id == thread_id,
                    m.AssistantRun.organization_id == self._org,
                )
                .order_by(m.AssistantRun.created_at.desc(), m.AssistantRun.id)
                .limit(limit)
            )
        ).all()
        return [_run(r) for r in reversed(rows)]


class SqlSpendLedger:
    """LLM spend per tenant, in the database, so caps hold across requests and restarts."""

    def __init__(self, session: AsyncSession, organization_id: UUID) -> None:
        self._s = session
        self._org = organization_id

    async def spent_since(self, organization_id: UUID, since: datetime) -> Decimal:
        total = await self._s.scalar(
            select(func.coalesce(func.sum(m.LlmUsage.cost_usd), 0)).where(
                m.LlmUsage.organization_id == organization_id, m.LlmUsage.created_at >= since
            )
        )
        return Decimal(total or 0)

    async def record(
        self, organization_id: UUID, amount_usd: Decimal, model: str, at: datetime
    ) -> None:
        self._s.add(
            m.LlmUsage(
                id=uuid4(),
                organization_id=organization_id,
                model=model[:100] or "unknown",
                cost_usd=amount_usd,
                created_at=at,
            )
        )
        await self._s.flush()
