"""A deterministic, offline embedder: hashed bag-of-words. No network, no cost.

Texts that share words get similar vectors, so retrieval behaves sensibly in tests, demos and
development. It is NOT a semantic model (it will not know that "cost per click" and "CPC" are the
same thing unless both phrasings appear), so it is never a substitute for a real embedding model
when judging answer quality.
"""

import hashlib
import itertools
import math
import re
from collections.abc import Sequence

from app.application.llm.cost import estimate_tokens
from app.application.ports.embedding import EMBEDDING_DIMENSIONS, EmbeddingResponse

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "its",
        "my",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "which",
        "why",
        "with",
        "you",
        "your",
        "do",
        "does",
        "did",
        "can",
    ]
)


def _bucket(word: str) -> tuple[int, float]:
    digest = hashlib.sha256(word.encode()).digest()
    index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
    sign = 1.0 if digest[4] % 2 == 0 else -1.0
    return index, sign


class HashingEmbedder:
    model = "hashing-bow-1536"

    async def embed(self, texts: Sequence[str]) -> EmbeddingResponse:
        vectors: list[list[float]] = []
        for text in texts:
            vec = [0.0] * EMBEDDING_DIMENSIONS
            words = [w for w in _WORD.findall(text.lower()) if w not in _STOP]
            for w in words:
                i, sign = _bucket(w)
                vec[i] += sign
            for a, b in itertools.pairwise(words):  # bigrams give some phrase sense
                i, sign = _bucket(f"{a}_{b}")
                vec[i] += 0.5 * sign
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vectors.append([x / norm for x in vec])
        return EmbeddingResponse(vectors, sum(estimate_tokens(t) for t in texts), self.model)
