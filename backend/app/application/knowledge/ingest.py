"""Ingest a document: chunk it, embed it, store it. Unchanged documents are skipped."""

import hashlib
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.application.knowledge.chunking import chunk_markdown
from app.application.ports.embedding import EMBEDDING_DIMENSIONS, EmbeddingClient
from app.application.ports.knowledge_store import KnowledgeStore, NewChunk, NewDocument, SourceType


@dataclass(frozen=True)
class IngestResult:
    source_key: str
    status: Literal["created", "replaced", "unchanged"]
    chunks: int
    embedding_tokens: int = 0


async def ingest_document(
    store: KnowledgeStore,
    embedder: EmbeddingClient,
    *,
    source_key: str,
    title: str,
    source_type: SourceType,
    text: str,
    organization_id: UUID | None = None,
    uri: str | None = None,
) -> IngestResult:
    chunks = chunk_markdown(text)
    if not chunks:
        raise ValueError(f"{source_key}: document has no content to index")
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing = await store.document_state(organization_id, source_key)
    if (
        existing
        and existing.content_hash == content_hash
        and existing.embedding_model == embedder.model
    ):
        return IngestResult(source_key, "unchanged", len(chunks))  # nothing to pay for

    response = await embedder.embed([c.embedding_text for c in chunks])
    if len(response.vectors) != len(chunks) or any(
        len(v) != EMBEDDING_DIMENSIONS for v in response.vectors
    ):
        raise ValueError(f"embedder returned vectors that do not match {EMBEDDING_DIMENSIONS} dims")
    await store.replace_document(
        NewDocument(source_key, title, source_type, content_hash, organization_id, uri),
        [
            NewChunk(c.ordinal, c.heading, c.content, vec, response.model)
            for c, vec in zip(chunks, response.vectors, strict=True)
        ],
    )
    return IngestResult(
        source_key, "replaced" if existing else "created", len(chunks), response.tokens
    )
