"""In-memory AssistantStore for tests (same contract as the SQL store)."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.application.assistant.loop import AssistantRun
from app.application.assistant.records import (
    answer_record,
    evidence_records,
    trace_records,
    violation_records,
)
from app.application.ports.assistant_store import StoredMessage, StoredRun


class MemoryAssistantStore:
    def __init__(self) -> None:
        self.threads: dict[UUID, str] = {}
        self._messages: dict[UUID, list[StoredMessage]] = {}
        self._runs: dict[UUID, StoredRun] = {}

    async def create_thread(self, title: str) -> UUID:
        thread_id = uuid4()
        self.threads[thread_id] = title
        self._messages[thread_id] = []
        return thread_id

    async def thread_exists(self, thread_id: UUID) -> bool:
        return thread_id in self.threads

    async def messages(self, thread_id: UUID, limit: int = 20) -> list[StoredMessage]:
        return self._messages[thread_id][-limit:]

    async def save_turn(self, thread_id: UUID, run: AssistantRun, cost_usd: Decimal) -> UUID:
        run_id, now = uuid4(), datetime.now(UTC)
        self._runs[run_id] = StoredRun(
            id=run_id,
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
            created_at=now,
        )
        self._messages[thread_id] += [
            StoredMessage(uuid4(), "user", run.question, None, now),
            StoredMessage(uuid4(), "assistant", run.answer_text, run_id, now),
        ]
        return run_id

    async def get_run(self, run_id: UUID) -> StoredRun | None:
        return self._runs.get(run_id)

    async def runs(self, thread_id: UUID, limit: int = 20) -> list[StoredRun]:
        return [r for r in self._runs.values() if r.thread_id == thread_id][-limit:]
