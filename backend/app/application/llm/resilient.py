"""Timeouts and retries with exponential backoff for transient LLM failures.

Only network trouble, provider 5xx and rate limits are retried. Bad requests, authentication
failures, spend-cap refusals and malformed responses fail immediately: retrying cannot fix them
and would waste money or hide a bug.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence

import structlog

from app.application.ports.llm import (
    LLMClient,
    LLMRateLimitedError,
    LLMResponse,
    LLMUnavailableError,
    Message,
    ResponseSchema,
    ToolSpec,
)

log = structlog.get_logger()


class ResilientLLMClient:
    def __init__(
        self,
        inner: LLMClient,
        *,
        max_retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
        timeout_seconds: float = 60.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if max_retries < 0 or timeout_seconds <= 0:
            raise ValueError("max_retries must be >= 0 and timeout_seconds > 0")
        self._inner = inner
        self._max_retries = max_retries
        self._base = base_delay
        self._max_delay = max_delay
        self._timeout = timeout_seconds
        self._sleep = sleep
        self._jitter = jitter

    def _delay(self, attempt: int, error: Exception) -> float:
        if isinstance(error, LLMRateLimitedError) and error.retry_after is not None:
            return min(self._max_delay, max(0.0, error.retry_after))
        backoff = min(self._max_delay, self._base * 2**attempt)
        return float(backoff * (0.5 + self._jitter() / 2))  # 50-100% of the backoff

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        response_schema: ResponseSchema | None = None,
        max_output_tokens: int = 1024,
        temperature: float | None = 0.0,
    ) -> LLMResponse:
        attempt = 0
        while True:
            try:
                return await asyncio.wait_for(
                    self._inner.complete(
                        messages,
                        tools=tools,
                        response_schema=response_schema,
                        max_output_tokens=max_output_tokens,
                        temperature=temperature,
                    ),
                    timeout=self._timeout,
                )
            except TimeoutError:
                error: Exception = LLMUnavailableError("LLM call timed out")
            except (LLMUnavailableError, LLMRateLimitedError) as exc:
                error = exc
            if attempt >= self._max_retries:
                raise error
            delay = self._delay(attempt, error)
            log.warning("llm_retry", attempt=attempt + 1, error=type(error).__name__, delay_s=delay)
            await self._sleep(delay)
            attempt += 1
