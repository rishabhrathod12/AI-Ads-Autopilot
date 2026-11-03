"""ORM models for the knowledge base: documents and their embedded chunks (pgvector)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from app.application.ports.embedding import EMBEDDING_DIMENSIONS
from app.infrastructure.db.models import Base

_NOW = text("clock_timestamp()")


class Vector(UserDefinedType[Any]):
    """pgvector column type. Values are written and compared through raw SQL casts, so no
    python-side bind/result processing is needed (and no extra dependency)."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return f"vector({EMBEDDING_DIMENSIONS})"


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    # NULL = a global document shipped with the product; otherwise owned by one organisation.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    source_key: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    uri: Mapped[str | None] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW, nullable=False
    )

    __table_args__ = (
        Index(
            "uq_knowledge_documents_global_key",
            "source_key",
            unique=True,
            postgresql_where=text("organization_id IS NULL"),
        ),
        Index(
            "uq_knowledge_documents_org_key",
            "organization_id",
            "source_key",
            unique=True,
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (UniqueConstraint("document_id", "ordinal"),)
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalised from the document so row-level security can be evaluated on the chunk itself.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False)
