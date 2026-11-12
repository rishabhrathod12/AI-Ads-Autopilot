"""ORM models: recommendations, approval_records (immutable), execution_attempts (append-only)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.models import Base


class RecommendationRow(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        UniqueConstraint("organization_id", "content_hash", name="uq_recommendations_org_hash"),
        CheckConstraint("type IN ('bid_change', 'budget_change', 'negative_keyword')"),
        CheckConstraint("status IN ('pending', 'approved', 'rejected', 'expired')"),
        CheckConstraint("data_source IN ('MOCK', 'AMAZON_ADS_API')"),
        CheckConstraint("expires_at > created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_label: Mapped[str] = mapped_column(String(500), nullable=False)
    current_value: Mapped[int | None] = mapped_column(BigInteger)
    proposed_value: Mapped[int | None] = mapped_column(BigInteger)
    proposed_text: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    expected_impact: Mapped[str] = mapped_column(Text, nullable=False)
    data_source: Mapped[str] = mapped_column(String(30), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApprovalRow(Base):
    __tablename__ = "approval_records"
    __table_args__ = (CheckConstraint("decision IN ('approve', 'reject')"),)
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    # UNIQUE: at most one decision per recommendation, enforced by the database.
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendations.id"), nullable=False, unique=True
    )
    decided_by: Mapped[str] = mapped_column(String(255), nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExecutionAttemptRow(Base):
    __tablename__ = "execution_attempts"
    __table_args__ = (CheckConstraint("status IN ('blocked')"),)  # nothing else exists yet
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendations.id"), nullable=False, index=True
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approval_records.id"))
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
