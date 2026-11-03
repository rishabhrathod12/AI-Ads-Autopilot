"""Hard per-tenant spend caps for LLM calls.

A call is refused *before* it is made if its worst-case cost (estimated input plus the full output
allowance) could breach the daily or monthly cap. Actual cost is recorded afterwards.

Known limit: two calls in flight at the same moment can both pass the check, so the cap can be
overshot by about one call's worst case. A database-backed ledger can serialise this later.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

import structlog

from app.application.llm.cost import Pricing, estimate_input_tokens
from app.application.ports.llm import (
    LLMBudgetExceededError,
    LLMClient,
    LLMResponse,
    Message,
    ResponseSchema,
    ToolSpec,
    Usage,
)

log = structlog.get_logger()


class SpendLedger(Protocol):
    async def spent_since(self, organization_id: UUID, since: datetime) -> Decimal: ...

    async def record(
        self, organization_id: UUID, amount_usd: Decimal, model: str, at: datetime
    ) -> None: ...


class InMemorySpendLedger:
    """For tests and single-process development; not shared between processes."""

    def __init__(self) -> None:
        self.entries: list[tuple[UUID, Decimal, str, datetime]] = []

    async def spent_since(self, organization_id: UUID, since: datetime) -> Decimal:
        return sum(
            (amt for org, amt, _, at in self.entries if org == organization_id and at >= since),
            Decimal(0),
        )

    async def record(
        self, organization_id: UUID, amount_usd: Decimal, model: str, at: datetime
    ) -> None:
        self.entries.append((organization_id, amount_usd, model, at))


@dataclass(frozen=True)
class SpendLimits:
    daily_usd: Decimal
    monthly_usd: Decimal

    def __post_init__(self) -> None:
        if self.daily_usd <= 0 or self.monthly_usd <= 0:
            raise ValueError("spend caps must be positive")


class BudgetedLLMClient:
    def __init__(
        self,
        inner: LLMClient,
        ledger: SpendLedger,
        organization_id: UUID,
        limits: SpendLimits,
        pricing: Pricing,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._inner = inner
        self._ledger = ledger
        self._org = organization_id
        self._limits = limits
        self._pricing = pricing
        self._clock = clock

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        response_schema: ResponseSchema | None = None,
        max_output_tokens: int = 1024,
        temperature: float | None = 0.0,
    ) -> LLMResponse:
        now = self._clock()
        worst = self._pricing.cost(Usage(estimate_input_tokens(messages, tools), max_output_tokens))
        day = datetime(now.year, now.month, now.day, tzinfo=UTC)
        month = day.replace(day=1)
        if await self._ledger.spent_since(self._org, day) + worst > self._limits.daily_usd:
            raise LLMBudgetExceededError("daily", self._limits.daily_usd)
        if await self._ledger.spent_since(self._org, month) + worst > self._limits.monthly_usd:
            raise LLMBudgetExceededError("monthly", self._limits.monthly_usd)

        response = await self._inner.complete(
            messages,
            tools=tools,
            response_schema=response_schema,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        cost = self._pricing.cost(response.usage)
        await self._ledger.record(self._org, cost, response.model, self._clock())
        log.info(
            "llm_call",
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            finish_reason=response.finish_reason,
            cost_usd=str(cost),
        )  # metadata only: never prompts or responses
        return response
