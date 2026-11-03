"""Persistence port for the knowledge base (reference material the assistant can search)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

SourceType = Literal["glossary", "guideline", "seller_note"]
Scope = Literal["global", "organization"]


@dataclass(frozen=True)
class NewDocument:
    source_key: str  # stable id, e.g. "kb/glossary"
    title: str
    source_type: SourceType
    content_hash: str
    organization_id: UUID | None = None  # None = global (ours, read-only to tenants)
    uri: str | None = None


@dataclass(frozen=True)
class NewChunk:
    ordinal: int
    heading: str
    content: str
    embedding: list[float]
    embedding_model: str


@dataclass(frozen=True)
class DocumentState:
    content_hash: str
    embedding_model: str


@dataclass(frozen=True)
class DocumentInfo:
    source_key: str
    title: str
    source_type: str
    scope: Scope
    chunks: int


@dataclass(frozen=True)
class Passage:
    """A retrieved chunk with the attribution a reader needs to check it."""

    title: str
    source_key: str
    heading: str
    ordinal: int
    content: str
    score: float  # cosine similarity, higher is closer
    scope: Scope


class KnowledgeStore(Protocol):
    async def document_state(
        self, organization_id: UUID | None, source_key: str
    ) -> DocumentState | None: ...

    async def replace_document(self, doc: NewDocument, chunks: Sequence[NewChunk]) -> None:
        """Replace any existing document with the same (scope, source_key)."""
        ...

    async def search(self, query: Sequence[float], model: str, limit: int) -> list[Passage]:
        """Closest chunks (global plus this tenant's own) embedded with ``model``, best first."""
        ...

    async def documents(self) -> list[DocumentInfo]: ...
