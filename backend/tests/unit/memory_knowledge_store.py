"""An in-memory KnowledgeStore for unit tests: same contract as the SQL store, exact cosine."""

import math
from collections.abc import Sequence
from uuid import UUID

from app.application.ports.knowledge_store import (
    DocumentInfo,
    DocumentState,
    NewChunk,
    NewDocument,
    Passage,
)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class MemoryKnowledgeStore:
    def __init__(self, organization_id: UUID | None = None) -> None:
        self.org = organization_id  # the reader's tenant
        self.docs: dict[tuple[UUID | None, str], tuple[NewDocument, list[NewChunk]]] = {}
        self.writes = 0

    async def document_state(
        self, organization_id: UUID | None, source_key: str
    ) -> DocumentState | None:
        found = self.docs.get((organization_id, source_key))
        if found is None:
            return None
        doc, chunks = found
        models = {c.embedding_model for c in chunks}
        return DocumentState(doc.content_hash, models.pop() if len(models) == 1 else "mixed")

    async def replace_document(self, doc: NewDocument, chunks: Sequence[NewChunk]) -> None:
        self.writes += 1
        self.docs[(doc.organization_id, doc.source_key)] = (doc, list(chunks))

    async def search(self, query: Sequence[float], model: str, limit: int) -> list[Passage]:
        scored: list[tuple[float, str, int, Passage]] = []
        for (org, key), (doc, chunks) in self.docs.items():
            if org is not None and org != self.org:
                continue
            for c in chunks:
                if c.embedding_model != model:
                    continue
                score = _cosine(query, c.embedding)
                p = Passage(
                    doc.title,
                    key,
                    c.heading,
                    c.ordinal,
                    c.content,
                    score,
                    "global" if org is None else "organization",
                )
                scored.append((score, key, c.ordinal, p))
        scored.sort(key=lambda t: (-t[0], t[1], t[2]))
        return [t[3] for t in scored[:limit]]

    async def documents(self) -> list[DocumentInfo]:
        return [
            DocumentInfo(
                key, d.title, d.source_type, "global" if org is None else "organization", len(cs)
            )
            for (org, key), (d, cs) in sorted(self.docs.items(), key=lambda kv: kv[0][1])
            if org is None or org == self.org
        ]
