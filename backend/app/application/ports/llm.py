"""LLMClient port: what the assistant depends on (never a vendor SDK).

Deliberately no token streaming. The assistant's answers are checked by code (every number must
trace to a tool result) before anyone sees them, so streaming raw model tokens would show
unverified numbers. The API streams *events* (tool calls, tool results, the validated final answer).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]
FinishReason = Literal["stop", "tool_calls", "length", "content_filter"]


class LLMError(RuntimeError):
    """Base class. Messages never contain secrets or prompt/response content."""


class LLMNotConfiguredError(LLMError):
    """No LLM provider is configured (the default)."""


class LLMAuthError(LLMError):
    """The provider rejected our credentials. Not retried."""


class LLMBadRequestError(LLMError):
    """The provider rejected the request itself. Not retried."""


class LLMRateLimitedError(LLMError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMUnavailableError(LLMError):
    """Network failure, timeout or provider 5xx. Retried."""


class LLMResponseError(LLMError):
    """The provider answered, but not with a usable response (malformed JSON, no choices...)."""


class LLMBudgetExceededError(LLMError):
    def __init__(self, scope: Literal["daily", "monthly"], cap_usd: Decimal) -> None:
        super().__init__(f"{scope} LLM spend cap of {cap_usd} USD would be exceeded")
        self.scope = scope
        self.cap_usd = cap_usd


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the arguments


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None  # set on role="tool" messages


@dataclass(frozen=True)
class ResponseSchema:
    """Ask for a JSON object conforming to ``schema`` (structured output)."""

    name: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class LLMResponse:
    message: Message
    usage: Usage
    model: str
    finish_reason: FinishReason
    raw_id: str | None = field(default=None, compare=False)


class LLMClient(Protocol):
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        response_schema: ResponseSchema | None = None,
        max_output_tokens: int = 1024,
        temperature: float | None = 0.0,
    ) -> LLMResponse: ...
