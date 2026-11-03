"""Knowledge base: documents and embedded chunks (pgvector).

No ANN index on purpose: the corpus is a few hundred chunks, where an exact scan is both
cheaper to maintain and exactly correct (see ADR 0017).

Revision ID: 0005
Revises: 0004
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

APP_ROLE = "adpilot_app"
TENANT = "nullif(current_setting('app.org_id', true), '')::uuid"
UUID = postgresql.UUID(as_uuid=True)
NOW = sa.text("clock_timestamp()")
DIMENSIONS = 1536


class _Vector(UserDefinedType):  # type: ignore[type-arg]
    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return f"vector({DIMENSIONS})"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "knowledge_documents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("source_key", sa.String(200), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("uri", sa.String(500)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.CheckConstraint("source_type IN ('glossary','guideline','seller_note')"),
    )
    op.create_index(
        "ix_knowledge_documents_organization_id", "knowledge_documents", ["organization_id"]
    )
    op.create_index(
        "uq_knowledge_documents_global_key",
        "knowledge_documents",
        ["source_key"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )
    op.create_index(
        "uq_knowledge_documents_org_key",
        "knowledge_documents",
        ["organization_id", "source_key"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "document_id",
            UUID,
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("heading", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", _Vector(), nullable=False),
        sa.Column("embedding_model", sa.String(100), nullable=False),
        sa.UniqueConstraint("document_id", "ordinal"),
    )
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])
    op.create_index("ix_knowledge_chunks_organization_id", "knowledge_chunks", ["organization_id"])

    for table in ("knowledge_documents", "knowledge_chunks"):
        # ENABLE but not FORCE: the table owner (the ingestion CLI) writes global documents,
        # which no tenant may do. The application role is subject to the policy and read-only.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY read_global_or_own ON {table} FOR SELECT "
            f"USING (organization_id IS NULL OR organization_id = {TENANT})"
        )
        op.execute(f"GRANT SELECT ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
