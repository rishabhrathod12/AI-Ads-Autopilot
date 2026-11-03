"""Retries and spend caps for embedding calls. Same rules as chat: refuse before spending."""

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import structlog

from app.application.llm.budget import SpendLedger, SpendLimits
from app.application.llm.cost import estimate_tokens
from app.application.ports.embedding import EmbeddingClient, EmbeddingResponse
from app.application.ports.llm import (
    LLMBudgetExceededError,
    LLMRateLimitedError,
    LLMUnavailableError,
)

log = structlog.get_logger()
_MILLION = Decimal(1_000_000)


class ResilientEmbeddingClient:
    def __init__(
        self,
        inner: EmbeddingClient,
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
        self._base, self._max_delay, self._timeout = base_delay, max_delay, timeout_seconds
        self._sleep, self._jitter = sleep, jitter

    @property
    def model(self) -> str:
        return self._inner.model

    async def embed(self, texts: Sequence[str]) -> EmbeddingResponse:
        attempt = 0
        while True:
            try:
                return await asyncio.wait_for(self._inner.embed(texts), timeout=self._timeout)
            except TimeoutError:
                error: Exception = LLMUnavailableError("embedding call timed out")
            except (LLMUnavailableError, LLMRateLimitedError) as exc:
                error = exc
            if attempt >= self._max_retries:
                raise error
            if isinstance(error, LLMRateLimitedError) and error.retry_after is not None:
                delay = min(self._max_delay, max(0.0, error.retry_after))
            else:
                delay = float(
                    min(self._max_delay, self._base * 2**attempt) * (0.5 + self._jitter() / 2)
                )
            log.warning("embedding_retry", attempt=attempt + 1, error=type(error).__name__)
            await self._sleep(delay)
            attempt += 1


class BudgetedEmbeddingClient:
    def __init__(
        self,
        inner: EmbeddingClient,
        ledger: SpendLedger,
        organization_id: UUID,
        limits: SpendLimits,
        usd_per_million_tokens: Decimal,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if usd_per_million_tokens < 0:
            raise ValueError("price must not be negative")
        self._inner = inner
        self._ledger = ledger
        self._org = organization_id
        self._limits = limits
        self._price = usd_per_million_tokens
        self._clock = clock

    @property
    def model(self) -> str:
        return self._inner.model

    async def embed(self, texts: Sequence[str]) -> EmbeddingResponse:
        now = self._clock()
        worst = Decimal(sum(estimate_tokens(t) for t in texts)) * self._price / _MILLION
        day = datetime(now.year, now.month, now.day, tzinfo=UTC)
        if await self._ledger.spent_since(self._org, day) + worst > self._limits.daily_usd:
            raise LLMBudgetExceededError("daily", self._limits.daily_usd)
        month = day.replace(day=1)
        if await self._ledger.spent_since(self._org, month) + worst > self._limits.monthly_usd:
            raise LLMBudgetExceededError("monthly", self._limits.monthly_usd)
        response = await self._inner.embed(texts)
        cost = Decimal(response.tokens) * self._price / _MILLION
        await self._ledger.record(self._org, cost, response.model, self._clock())
        log.info("embedding_call", model=response.model, tokens=response.tokens, cost_usd=str(cost))
        return response
