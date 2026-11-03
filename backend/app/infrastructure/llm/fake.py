"""A scripted LLM for tests, evaluation harnesses and offline development. Spends nothing."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.application.llm.cost import estimate_tokens
from app.application.ports.llm import (
    LLMResponse,
    Message,
    ResponseSchema,
    ToolCall,
    ToolSpec,
    Usage,
)

Step = LLMResponse | Exception | Callable[[Sequence[Message], Sequence[ToolSpec]], LLMResponse]


@dataclass(frozen=True)
class Call:
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    response_schema: ResponseSchema | None
    max_output_tokens: int
    temperature: float | None


def text_reply(text: str, *, model: str = "scripted") -> LLMResponse:
    return LLMResponse(Message("assistant", text), Usage(100, estimate_tokens(text)), model, "stop")


def tool_reply(*calls: tuple[str, dict[str, Any]], model: str = "scripted") -> LLMResponse:
    tool_calls = tuple(ToolCall(f"call_{i}", name, args) for i, (name, args) in enumerate(calls))
    return LLMResponse(
        Message("assistant", None, tool_calls), Usage(100, 20 * len(calls)), model, "tool_calls"
    )


class ScriptedLLM:
    """Plays back ``script`` in order, recording every call it receives."""

    def __init__(self, script: Sequence[Step]) -> None:
        self._script = list(script)
        self.calls: list[Call] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        response_schema: ResponseSchema | None = None,
        max_output_tokens: int = 1024,
        temperature: float | None = 0.0,
    ) -> LLMResponse:
        self.calls.append(
            Call(tuple(messages), tuple(tools), response_schema, max_output_tokens, temperature)
        )
        if not self._script:
            raise AssertionError("ScriptedLLM was called more times than it was scripted for")
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(messages, tools)
        return step

    @property
    def remaining(self) -> int:
        return len(self._script)
