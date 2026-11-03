"""EmbeddingClient port. Failures reuse the LLM error types (same causes, same handling)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

EMBEDDING_DIMENSIONS = 1536  # fixed by the database column; models must produce exactly this


@dataclass(frozen=True)
class EmbeddingResponse:
    vectors: list[list[float]]
    tokens: int  # tokens billed (estimated when the provider does not say)
    model: str


class EmbeddingClient(Protocol):
    @property
    def model(self) -> str:
        """Identifies the embedding space. Vectors from different models are never compared."""
        ...

    async def embed(self, texts: Sequence[str]) -> EmbeddingResponse:
        """One vector per text, each of length ``EMBEDDING_DIMENSIONS``, unit-normalised or not."""
        ...
