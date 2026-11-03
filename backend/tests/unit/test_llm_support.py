"""Redaction, cost math, spend caps and retries: everything around an LLM call, with no LLM."""

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.application.llm.budget import (
    BudgetedLLMClient,
    InMemorySpendLedger,
    SpendLimits,
)
from app.application.llm.cost import Pricing, estimate_input_tokens, estimate_tokens
from app.application.llm.redact import REDACTED, redact
from app.application.llm.resilient import ResilientLLMClient
from app.application.ports.llm import (
    LLMAuthError,
    LLMBadRequestError,
    LLMBudgetExceededError,
    LLMRateLimitedError,
    LLMResponse,
    LLMResponseError,
    LLMUnavailableError,
    Message,
    ResponseSchema,
    ToolCall,
    ToolSpec,
    Usage,
)
from app.infrastructure.llm.fake import ScriptedLLM, Step, text_reply

D = Decimal
PRICING = Pricing(D(1), D(2))  # USD per million tokens
NOW = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)
ORG = uuid4()
HELLO = [Message("user", "hello")]


# ---- redaction -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-abcdef1234567890XYZ",
        "sk-abcdefgh12345678",
        "Bearer abc.def-1234567890",
        "api_key=supersecret123",
        "password: hunter2",
        "token = abc123def",
        "someone.name+tag@example.co.in",
        "AKIAABCDEFGHIJKLMNOP",
    ],
)
def test_secrets_and_emails_are_redacted(secret: str) -> None:
    out = redact(f"before {secret} after")
    assert secret not in out
    assert REDACTED in out
    assert out.startswith("before ")
    assert redact(out) == out  # idempotent


def test_ordinary_text_and_numbers_are_untouched() -> None:
    text = "ACOS rose 4.2 points to ₹1,23,456; the task-force reviewed it (ask-me later)"
    assert redact(text) == text


# ---- cost ----------------------------------------------------------------------------------
def test_cost_is_exact_decimal_arithmetic() -> None:
    p = Pricing(D("2.5"), D("10"))
    assert p.cost(Usage(1_000_000, 500_000)) == D("7.5")
    assert p.cost(Usage(0, 0)) == 0
    assert p.cost(Usage(1, 0)) == D("0.0000025")
    with pytest.raises(ValueError, match="negative"):
        Pricing(D(-1), D(1))


def test_token_estimate_is_pessimistic_and_counts_tools_and_calls() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcdefghi") == 3
    plain = estimate_input_tokens(HELLO, [])
    tool = ToolSpec("get_summary", "Summarise the account", {"type": "object", "properties": {}})
    with_tool = estimate_input_tokens(HELLO, [tool])
    assert with_tool > plain > 0
    call = Message("assistant", None, (ToolCall("1", "get_summary", {"days": 28}),))
    assert estimate_input_tokens([*HELLO, call], []) > plain


# ---- spend caps ----------------------------------------------------------------------------
def budgeted(
    script: list[Step],
    ledger: InMemorySpendLedger,
    daily: str = "0.01",
    monthly: str = "1",
    org: UUID = ORG,
    clock: Callable[[], datetime] = lambda: NOW,
) -> tuple[BudgetedLLMClient, ScriptedLLM]:
    inner = ScriptedLLM(script)
    limits = SpendLimits(D(daily), D(monthly))
    return BudgetedLLMClient(inner, ledger, org, limits, PRICING, clock), inner


async def test_a_call_within_the_cap_is_made_and_its_actual_cost_recorded() -> None:
    ledger = InMemorySpendLedger()
    client, inner = budgeted([text_reply("hi")], ledger)
    await client.complete(HELLO, max_output_tokens=100)
    assert len(inner.calls) == 1
    (entry,) = ledger.entries
    assert entry[0] == ORG
    assert entry[1] == PRICING.cost(Usage(100, estimate_tokens("hi")))
    assert entry[2] == "scripted"


async def test_a_call_that_could_breach_the_daily_cap_is_refused_before_it_is_made() -> None:
    ledger = InMemorySpendLedger()
    await ledger.record(ORG, D("0.0095"), "m", NOW)  # almost at the 0.01 cap
    client, inner = budgeted([text_reply("hi")], ledger)
    with pytest.raises(LLMBudgetExceededError) as exc:
        await client.complete(HELLO, max_output_tokens=1000)  # worst case ~0.002
    assert exc.value.scope == "daily"
    assert inner.calls == []  # never reached the provider
    assert len(ledger.entries) == 1  # nothing new recorded


async def test_the_monthly_cap_is_enforced_separately() -> None:
    ledger = InMemorySpendLedger()
    await ledger.record(
        ORG, D("0.99"), "m", datetime(2026, 11, 2, tzinfo=UTC)
    )  # earlier this month
    client, inner = budgeted([text_reply("hi")], ledger, daily="5", monthly="1")
    with pytest.raises(LLMBudgetExceededError) as exc:
        await client.complete(HELLO, max_output_tokens=10_000)
    assert exc.value.scope == "monthly"
    assert inner.calls == []


async def test_yesterdays_spend_counts_toward_the_month_but_not_the_day() -> None:
    ledger = InMemorySpendLedger()
    await ledger.record(ORG, D("0.0095"), "m", datetime(2026, 11, 14, 23, 0, tzinfo=UTC))
    client, _ = budgeted([text_reply("hi")], ledger)  # daily cap 0.01
    await client.complete(HELLO, max_output_tokens=100)  # fine: a new day


async def test_last_months_spend_does_not_count_this_month() -> None:
    ledger = InMemorySpendLedger()
    await ledger.record(ORG, D("0.9"), "m", datetime(2026, 10, 31, 23, 0, tzinfo=UTC))
    client, _ = budgeted([text_reply("hi")], ledger, daily="5", monthly="1")
    await client.complete(HELLO, max_output_tokens=100)


async def test_tenants_have_separate_budgets() -> None:
    ledger = InMemorySpendLedger()
    other = uuid4()
    await ledger.record(other, D("5"), "m", NOW)  # someone else spent everything
    client, _ = budgeted([text_reply("hi")], ledger)
    await client.complete(HELLO, max_output_tokens=100)


async def test_a_failed_call_is_not_charged() -> None:
    ledger = InMemorySpendLedger()
    client, _ = budgeted([LLMUnavailableError("down")], ledger)
    with pytest.raises(LLMUnavailableError):
        await client.complete(HELLO)
    assert ledger.entries == []


def test_spend_caps_must_be_positive() -> None:
    for daily, monthly in ((0, 1), (1, 0), (-1, 1)):
        with pytest.raises(ValueError, match="positive"):
            SpendLimits(D(daily), D(monthly))


# ---- retries and timeouts ------------------------------------------------------------------
def resilient(script: list[Step], **kw: Any) -> tuple[ResilientLLMClient, ScriptedLLM, list[float]]:
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    inner = ScriptedLLM(script)
    client = ResilientLLMClient(inner, sleep=fake_sleep, jitter=lambda: 1.0, **kw)
    return client, inner, sleeps


async def test_transient_failures_are_retried_with_exponential_backoff() -> None:
    client, inner, sleeps = resilient(
        [LLMUnavailableError("a"), LLMUnavailableError("b"), text_reply("ok")]
    )
    result = await client.complete(HELLO)
    assert result.message.content == "ok"
    assert len(inner.calls) == 3
    assert sleeps == [0.5, 1.0]


async def test_rate_limits_honour_retry_after_but_capped() -> None:
    client, _, sleeps = resilient(
        [LLMRateLimitedError("slow", 3.0), LLMRateLimitedError("slower", 100.0), text_reply("ok")]
    )
    await client.complete(HELLO)
    assert sleeps == [3.0, 8.0]  # 100s is capped at max_delay


async def test_it_gives_up_after_max_retries_and_raises_the_last_error() -> None:
    client, inner, sleeps = resilient(
        [LLMUnavailableError(str(i)) for i in range(4)], max_retries=3
    )
    with pytest.raises(LLMUnavailableError, match="3"):
        await client.complete(HELLO)
    assert len(inner.calls) == 4
    assert len(sleeps) == 3


@pytest.mark.parametrize(
    "error",
    [
        LLMAuthError("no"),
        LLMBadRequestError("bad"),
        LLMResponseError("garbled"),
        LLMBudgetExceededError("daily", D(1)),
    ],
)
async def test_errors_that_retrying_cannot_fix_fail_immediately(error: Exception) -> None:
    client, inner, sleeps = resilient([error, text_reply("never")])
    with pytest.raises(type(error)):
        await client.complete(HELLO)
    assert len(inner.calls) == 1
    assert sleeps == []


async def test_a_hung_call_times_out_and_is_retried_then_reported_as_unavailable() -> None:
    class Hangs:
        calls = 0

        async def complete(
            self,
            messages: Sequence[Message],
            *,
            tools: Sequence[ToolSpec] = (),
            response_schema: ResponseSchema | None = None,
            max_output_tokens: int = 1024,
            temperature: float | None = 0.0,
        ) -> LLMResponse:
            Hangs.calls += 1
            await asyncio.sleep(5)
            return text_reply("too late")

    async def no_sleep(_: float) -> None:
        return None

    client = ResilientLLMClient(
        Hangs(),
        max_retries=1,
        timeout_seconds=0.05,
        sleep=no_sleep,
    )
    with pytest.raises(LLMUnavailableError, match="timed out"):
        await client.complete(HELLO)
    assert Hangs.calls == 2


async def test_arguments_pass_through_to_the_inner_client() -> None:
    client, inner, _ = resilient([text_reply("ok")])
    tool = ToolSpec("t", "d", {"type": "object"})
    await client.complete(HELLO, tools=[tool], max_output_tokens=77, temperature=None)
    call = inner.calls[0]
    assert call.tools == (tool,)
    assert call.max_output_tokens == 77
    assert call.temperature is None


def test_resilience_settings_are_validated() -> None:
    inner = ScriptedLLM([])
    with pytest.raises(ValueError):
        ResilientLLMClient(inner, max_retries=-1)
    with pytest.raises(ValueError):
        ResilientLLMClient(inner, timeout_seconds=0)
