"""ORM models for the assistant: threads, messages, immutable run traces, and LLM usage."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.models import Base

# clock_timestamp(), not now(): now() is the transaction start, so rows written in one
# transaction would tie and "latest" / ordering would be arbitrary.
_NOW = text("clock_timestamp()")


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=_NOW, nullable=False)


def _org() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)


class AssistantThread(Base):
    __tablename__ = "assistant_threads"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = _org()
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = _created()


class AssistantRun(Base):
    """One question and everything that happened answering it. Immutable."""

    __tablename__ = "assistant_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('answered','partial','unverified','step_limit','budget_exceeded',"
            "'llm_unavailable','llm_error','refused','not_configured')"
        ),
        CheckConstraint("input_tokens >= 0 AND output_tokens >= 0 AND cost_usd >= 0"),
        Index("ix_assistant_runs_thread", "thread_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = _org()
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assistant_threads.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    answer: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    trace: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    violations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created()


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')"),
        Index("ix_assistant_messages_thread", "thread_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = _org()
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assistant_threads.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assistant_runs.id"))
    created_at: Mapped[datetime] = _created()


class LlmUsage(Base):
    """One row per model call: the spend ledger behind the per-tenant caps."""

    __tablename__ = "llm_usage"
    __table_args__ = (
        CheckConstraint("cost_usd >= 0"),
        Index("ix_llm_usage_org_time", "organization_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = _org()
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    created_at: Mapped[datetime] = _created()
