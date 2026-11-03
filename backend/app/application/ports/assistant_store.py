"""Persistence port for assistant conversations. Runs are immutable records."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import UUID

from app.application.assistant.loop import AssistantRun


@dataclass(frozen=True)
class StoredMessage:
    id: UUID
    role: Literal["user", "assistant"]
    content: str
    run_id: UUID | None
    created_at: datetime


@dataclass(frozen=True)
class StoredRun:
    id: UUID
    thread_id: UUID
    question: str
    status: str
    answer: dict[str, Any]  # claims, caveats, confidence, system notes, rendered text
    evidence: list[dict[str, Any]]  # every tool result the answer was built from
    trace: list[dict[str, Any]]  # what happened, step by step (no prompt text)
    violations: list[dict[str, Any]]
    flags: list[str]
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    llm_calls: int
    tool_calls: int
    validation_attempts: int
    created_at: datetime


class AssistantStore(Protocol):
    async def create_thread(self, title: str) -> UUID: ...

    async def thread_exists(self, thread_id: UUID) -> bool: ...

    async def messages(self, thread_id: UUID, limit: int = 20) -> list[StoredMessage]:
        """The most recent ``limit`` messages, oldest first."""
        ...

    async def save_turn(self, thread_id: UUID, run: AssistantRun, cost_usd: Decimal) -> UUID:
        """Persist a finished run plus its user and assistant messages, atomically."""
        ...

    async def get_run(self, run_id: UUID) -> StoredRun | None: ...

    async def runs(self, thread_id: UUID, limit: int = 20) -> list[StoredRun]: ...
