"""Token and cost estimation. Prices are configuration, never hard-coded: vendors change them."""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.application.ports.llm import Message, ToolSpec, Usage

_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Pricing:
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal

    def __post_init__(self) -> None:
        if self.input_usd_per_million < 0 or self.output_usd_per_million < 0:
            raise ValueError("prices must not be negative")

    def cost(self, usage: Usage) -> Decimal:
        return (
            Decimal(usage.input_tokens) * self.input_usd_per_million
            + Decimal(usage.output_tokens) * self.output_usd_per_million
        ) / _MILLION


def estimate_tokens(text: str) -> int:
    """Deliberately pessimistic (about 3 characters per token): used only to refuse a call that
    could breach a spend cap, so over-estimating is the safe error."""
    return math.ceil(len(text) / 3)


def estimate_input_tokens(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> int:
    total = 0
    for m in messages:
        total += 4 + estimate_tokens(m.content or "")
        for call in m.tool_calls:
            total += estimate_tokens(call.name) + estimate_tokens(json.dumps(call.arguments))
    for t in tools:
        total += estimate_tokens(t.name + t.description + json.dumps(t.parameters))
    return total
