"""SQL knowledge store: exact cosine search over pgvector, tenant-aware.

Vectors cross the driver as text and are cast in SQL, so no pgvector python package is needed.
Reads run on the tenant-scoped session (row-level security shows global + own documents);
writes (ingestion) run on the owner connection, which is the only way to write global documents.
"""

from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.knowledge_store import (
    DocumentInfo,
    DocumentState,
    NewChunk,
    NewDocument,
    Passage,
)
from app.infrastructure.db import knowledge_models as m

_INSERT_CHUNK = text(
    "INSERT INTO knowledge_chunks (id, document_id, organization_id, ordinal, heading, content, "
    "embedding, embedding_model) VALUES (:id, :doc, :org, :ordinal, :heading, :content, "
    "CAST(CAST(:emb AS text) AS vector), :model)"
)
_SEARCH = text(
    "SELECT d.title, d.source_key, c.heading, c.ordinal, c.content, d.organization_id, "
    "1 - (c.embedding <=> CAST(CAST(:q AS text) AS vector)) AS score "
    "FROM knowledge_chunks c JOIN knowledge_documents d ON d.id = c.document_id "
    "WHERE c.embedding_model = :model "
    "AND (c.organization_id IS NULL OR c.organization_id = CAST(:org AS uuid)) "
    "ORDER BY c.embedding <=> CAST(CAST(:q AS text) AS vector), d.source_key, c.ordinal "
    "LIMIT :limit"
)


def vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def _doc_filter(organization_id: UUID | None, source_key: str) -> tuple[ColumnElement[bool], ...]:
    org_clause = (
        m.KnowledgeDocument.organization_id.is_(None)
        if organization_id is None
        else m.KnowledgeDocument.organization_id == organization_id
    )
    return (org_clause, m.KnowledgeDocument.source_key == source_key)


class SqlKnowledgeStore:
    def __init__(self, session: AsyncSession, organization_id: UUID | None = None) -> None:
        self._s = session
        self._org = organization_id  # the reader's tenant; None sees only global documents

    async def document_state(
        self, organization_id: UUID | None, source_key: str
    ) -> DocumentState | None:
        row = (
            await self._s.execute(
                select(m.KnowledgeDocument.id, m.KnowledgeDocument.content_hash).where(
                    *_doc_filter(organization_id, source_key)
                )
            )
        ).first()
        if row is None:
            return None
        models = (
            await self._s.scalars(
                select(m.KnowledgeChunk.embedding_model)
                .where(m.KnowledgeChunk.document_id == row.id)
                .distinct()
            )
        ).all()
        # A document is "current" only if every chunk used one model; mixed means re-embed.
        model = models[0] if len(models) == 1 else "mixed"
        return DocumentState(row.content_hash, model)

    async def replace_document(self, doc: NewDocument, chunks: Sequence[NewChunk]) -> None:
        await self._s.execute(
            delete(m.KnowledgeDocument).where(*_doc_filter(doc.organization_id, doc.source_key))
        )  # chunks go with it (ON DELETE CASCADE)
        doc_id = uuid4()
        self._s.add(
            m.KnowledgeDocument(
                id=doc_id,
                organization_id=doc.organization_id,
                source_key=doc.source_key,
                title=doc.title,
                source_type=doc.source_type,
                uri=doc.uri,
                content_hash=doc.content_hash,
            )
        )
        await self._s.flush()
        for c in chunks:
            await self._s.execute(
                _INSERT_CHUNK,
                {
                    "id": uuid4(),
                    "doc": doc_id,
                    "org": doc.organization_id,
                    "ordinal": c.ordinal,
                    "heading": c.heading,
                    "content": c.content,
                    "emb": vector_literal(c.embedding),
                    "model": c.embedding_model,
                },
            )

    async def search(self, query: Sequence[float], model: str, limit: int) -> list[Passage]:
        org = str(self._org) if self._org else None
        rows = (
            await self._s.execute(
                _SEARCH,
                {"q": vector_literal(query), "model": model, "org": org, "limit": limit},
            )
        ).all()
        return [
            Passage(
                title=r.title,
                source_key=r.source_key,
                heading=r.heading,
                ordinal=r.ordinal,
                content=r.content,
                score=float(r.score),
                scope="global" if r.organization_id is None else "organization",
            )
            for r in rows
        ]

    async def documents(self) -> list[DocumentInfo]:
        rows = (
            await self._s.execute(
                select(
                    m.KnowledgeDocument.source_key,
                    m.KnowledgeDocument.title,
                    m.KnowledgeDocument.source_type,
                    m.KnowledgeDocument.organization_id,
                    func.count(m.KnowledgeChunk.id),
                )
                .join(m.KnowledgeChunk, m.KnowledgeChunk.document_id == m.KnowledgeDocument.id)
                .group_by(m.KnowledgeDocument.id)
                .order_by(m.KnowledgeDocument.source_key)
            )
        ).all()
        return [
            DocumentInfo(key, title, stype, "global" if org is None else "organization", n)
            for key, title, stype, org, n in rows
        ]
