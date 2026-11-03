"""OpenAI embeddings adapter over httpx. Verified only against a mocked transport so far.

Like the chat adapter, error messages carry the status and the provider's error code, never its
free text and never the key.
"""

from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import SecretStr

from app.application.llm.cost import estimate_tokens
from app.application.ports.embedding import EMBEDDING_DIMENSIONS, EmbeddingResponse
from app.application.ports.llm import (
    LLMAuthError,
    LLMBadRequestError,
    LLMRateLimitedError,
    LLMResponseError,
    LLMUnavailableError,
)

MAX_BATCH = 96  # inputs per request


def _error_code(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error") or {}
        return str(err.get("code") or err.get("type") or "unknown")
    except (ValueError, AttributeError):
        return "unknown"


def _raise_for_status(resp: httpx.Response) -> None:
    status = resp.status_code
    if status < 400:
        return
    if status in (401, 403):
        raise LLMAuthError(f"provider rejected the credentials (HTTP {status})")
    if status == 429:
        try:
            retry_after = float(resp.headers["retry-after"])
        except (KeyError, ValueError):
            retry_after = None
        raise LLMRateLimitedError(
            f"provider rate limit (HTTP 429, {_error_code(resp)})", retry_after
        )
    if status >= 500:
        raise LLMUnavailableError(f"provider error (HTTP {status})")
    raise LLMBadRequestError(f"provider rejected the request (HTTP {status}, {_error_code(resp)})")


class OpenAIEmbeddings:
    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._key = api_key
        self._model = model
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._http = http or httpx.AsyncClient()

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        await self._http.aclose()

    async def embed(self, texts: Sequence[str]) -> EmbeddingResponse:
        vectors: list[list[float]] = []
        tokens = 0
        for start in range(0, len(texts), MAX_BATCH):
            batch = list(texts[start : start + MAX_BATCH])
            data = await self._post(batch)
            try:
                items = sorted(data["data"], key=lambda d: d["index"])
                got = [[float(x) for x in item["embedding"]] for item in items]
                tokens += int(data.get("usage", {}).get("total_tokens", 0)) or sum(
                    estimate_tokens(t) for t in batch
                )
            except (KeyError, TypeError, ValueError):
                raise LLMResponseError("unusable embeddings response") from None
            if len(got) != len(batch) or any(len(v) != EMBEDDING_DIMENSIONS for v in got):
                raise LLMResponseError(
                    f"embeddings must be {EMBEDDING_DIMENSIONS}-dimensional, one per input"
                )
            vectors += got
        return EmbeddingResponse(vectors, tokens, self._model)

    async def _post(self, batch: list[str]) -> dict[str, Any]:
        body = {"model": self._model, "input": batch, "dimensions": EMBEDDING_DIMENSIONS}
        try:
            resp = await self._http.post(
                self._url,
                json=body,
                headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
            )
        except httpx.TimeoutException:
            raise LLMUnavailableError("provider request timed out") from None
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"network error ({type(exc).__name__})") from None
        _raise_for_status(resp)
        try:
            payload = resp.json()
        except ValueError:
            raise LLMResponseError("provider returned a non-JSON body") from None
        if not isinstance(payload, dict):
            raise LLMResponseError("unusable embeddings response")
        return payload
