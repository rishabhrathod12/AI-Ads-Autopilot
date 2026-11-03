"""The assistant loop: tool calls, then a structured final answer that must pass verification.

* The model ends every answer by calling ``final_answer`` (structured claims with evidence ids).
  Plain text can never be shown, so everything the seller sees has been checked.
* A rejected draft goes back once with specific corrections. If it is still wrong, only the claims
  that verify individually are kept (a *partial* answer); if none do, nothing unverified is shown.
* Every tool call gets a reply (providers require it), invalid input comes back as an error the
  model can fix, and the number of model calls, tool calls and retries is bounded.
* The system adds its own notes (MOCK data, maturing days) so they never depend on the model.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.application.assistant.evidence import Evidence, ToolError
from app.application.assistant.faithfulness import (
    Claim,
    DraftAnswer,
    ValidationResult,
    Violation,
    correction_message,
    validate_answer,
)
from app.application.assistant.prompts import NUDGE, SYSTEM_PROMPT
from app.application.assistant.tools import ToolContext, ToolRunner, tool_specs
from app.application.ports.llm import (
    LLMAuthError,
    LLMBadRequestError,
    LLMBudgetExceededError,
    LLMClient,
    LLMNotConfiguredError,
    LLMRateLimitedError,
    LLMResponse,
    LLMResponseError,
    LLMUnavailableError,
    Message,
    ToolCall,
    ToolSpec,
)

FINAL_TOOL = "final_answer"


class ClaimArg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=600, description="One short factual statement.")
    evidence_ids: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="Evidence ids (ev_1, ...) supporting this statement. Required with figures.",
    )


class FinalAnswerArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ClaimArg] = Field(min_length=1, max_length=12)
    caveats: list[str] = Field(
        default_factory=list, max_length=5, description="Limits of the data or of the answer."
    )
    confidence: Literal["high", "medium", "low"] = "medium"


def final_answer_spec() -> ToolSpec:
    schema = FinalAnswerArgs.model_json_schema()
    schema.pop("title", None)
    schema["additionalProperties"] = False
    return ToolSpec(
        FINAL_TOOL,
        "Submit the final answer for the seller as cited claims. Call this once, on its own, "
        "after you have the data you need.",
        schema,
    )


class Status(StrEnum):
    ANSWERED = "answered"
    PARTIAL = "partial"  # some claims could not be verified and were removed
    UNVERIFIED = "unverified"  # nothing could be verified: no figures shown
    STEP_LIMIT = "step_limit"
    BUDGET_EXCEEDED = "budget_exceeded"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_ERROR = "llm_error"
    REFUSED = "refused"
    NOT_CONFIGURED = "not_configured"


FALLBACK_TEXT = {
    Status.UNVERIFIED: (
        "I couldn't verify an answer against your data, so I'm not showing figures I can't back "
        "up. Try asking about a specific campaign or period."
    ),
    Status.STEP_LIMIT: "I couldn't finish within my limits. Try a narrower question.",
    Status.BUDGET_EXCEEDED: "The assistant's usage limit has been reached. Try again later.",
    Status.LLM_UNAVAILABLE: "The assistant is temporarily unavailable. Try again shortly.",
    Status.LLM_ERROR: "The assistant hit an unexpected problem and could not answer.",
    Status.REFUSED: "The model declined to answer that.",
    Status.NOT_CONFIGURED: "The assistant is not configured on this server.",
}


@dataclass(frozen=True)
class StepRecord:
    """One entry in the run trace. Never holds prompt text, only what happened."""

    index: int
    kind: Literal["llm", "tool", "validation"]
    name: str
    detail: str = ""
    ok: bool = True
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


@dataclass(frozen=True)
class AssistantRun:
    status: Status
    question: str
    claims: tuple[Claim, ...]
    caveats: tuple[str, ...]
    confidence: Literal["high", "medium", "low"]
    system_notes: tuple[str, ...]
    answer_text: str
    evidence: tuple[Evidence, ...]
    cited: frozenset[str]
    flags: tuple[str, ...]
    steps: tuple[StepRecord, ...]
    violations: tuple[Violation, ...]  # of the last rejected draft, for debugging and evaluation
    dropped_claims: int
    model: str
    input_tokens: int
    output_tokens: int
    llm_calls: int
    tool_calls: int
    validation_attempts: int


@dataclass
class _State:
    messages: list[Message]
    runner: ToolRunner
    steps: list[StepRecord] = field(default_factory=list)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    validation_attempts: int = 0
    text_nudges: int = 0
    last_violations: tuple[Violation, ...] = ()

    def step(
        self,
        kind: Literal["llm", "tool", "validation"],
        name: str,
        *,
        detail: str = "",
        ok: bool = True,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
    ) -> None:
        self.steps.append(
            StepRecord(
                len(self.steps) + 1, kind, name, detail, ok, input_tokens, output_tokens, latency_ms
            )
        )


def _system_notes(evidence: Sequence[Evidence]) -> tuple[str, ...]:
    notes: list[str] = []
    if any(e.synthetic for e in evidence):
        notes.append("This answer is based on MOCK (synthetic) data, not live Amazon data.")
    if any(
        e.latest_date and e.mature_through and e.mature_through < e.latest_date for e in evidence
    ):
        notes.append(
            "The most recent days are still inside Amazon's attribution window; sales there are "
            "under-reported, so comparisons use complete days only."
        )
    if any("possible_prompt_injection" in e.flags for e in evidence):
        notes.append(
            "Seller-supplied text in the data looked like instructions; it was treated as data."
        )
    return tuple(notes)


def render(claims: Sequence[Claim], caveats: Sequence[str], notes: Sequence[str]) -> str:
    parts = [c.text for c in claims]
    parts += [f"Note: {c}" for c in caveats]
    parts += [f"Note: {n}" for n in notes]
    return "\n".join(parts)


def _finish(
    state: _State,
    question: str,
    status: Status,
    *,
    claims: Sequence[Claim] = (),
    caveats: Sequence[str] = (),
    confidence: Literal["high", "medium", "low"] = "low",
    dropped: int = 0,
    violations: tuple[Violation, ...] = (),
) -> AssistantRun:
    evidence = tuple(state.runner.evidence)
    notes = _system_notes(evidence)
    if status is Status.PARTIAL:
        notes = (
            f"{dropped} statement(s) were removed because their figures could not be verified.",
            *notes,
        )
    text = (
        render(claims, caveats, notes)
        if status in (Status.ANSWERED, Status.PARTIAL)
        else "\n".join([FALLBACK_TEXT[status], *[f"Note: {n}" for n in notes]])
    )
    return AssistantRun(
        status=status,
        question=question,
        claims=tuple(claims),
        caveats=tuple(caveats),
        confidence=confidence,
        system_notes=notes,
        answer_text=text,
        evidence=evidence,
        cited=frozenset(i for c in claims for i in c.evidence_ids),
        flags=tuple(sorted({f for e in evidence for f in e.flags})),
        steps=tuple(state.steps),
        violations=violations,
        dropped_claims=dropped,
        model=state.model,
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        llm_calls=state.llm_calls,
        tool_calls=len(state.runner.evidence),
        validation_attempts=state.validation_attempts,
    )


def _salvage(draft: DraftAnswer, evidence: Sequence[Evidence]) -> tuple[list[Claim], list[str]]:
    """Keep the claims and caveats that verify on their own."""
    kept = [c for c in draft.claims if validate_answer(DraftAnswer((c,)), evidence).ok]
    caveats = [c for c in draft.caveats if validate_answer(DraftAnswer((), (c,)), evidence).ok]
    return kept, caveats


def _reply(call: ToolCall, text: str) -> Message:
    return Message("tool", text, tool_call_id=call.id)


async def run_assistant(
    llm: LLMClient,
    ctx: ToolContext,
    question: str,
    *,
    history: Sequence[tuple[Literal["user", "assistant"], str]] = (),
    max_llm_calls: int = 6,
    max_tool_calls: int = 8,
    max_validation_retries: int = 1,
    max_output_tokens: int = 1500,
) -> AssistantRun:
    messages = [Message("system", SYSTEM_PROMPT)]
    messages += [Message(role, text) for role, text in history]
    messages.append(Message("user", question))
    state = _State(messages, ToolRunner(ctx, max_calls=max_tool_calls))
    tools = [*tool_specs(knowledge=ctx.has_knowledge), final_answer_spec()]

    while state.llm_calls < max_llm_calls:
        started = time.monotonic()
        try:
            response = await llm.complete(
                state.messages, tools=tools, max_output_tokens=max_output_tokens, temperature=0.0
            )
        except LLMBudgetExceededError:
            return _finish(state, question, Status.BUDGET_EXCEEDED)
        except LLMNotConfiguredError:
            return _finish(state, question, Status.NOT_CONFIGURED)
        except (LLMUnavailableError, LLMRateLimitedError):
            return _finish(state, question, Status.LLM_UNAVAILABLE)
        except (LLMAuthError, LLMBadRequestError, LLMResponseError):
            return _finish(state, question, Status.LLM_ERROR)

        state.llm_calls += 1
        state.model = response.model or state.model
        state.input_tokens += response.usage.input_tokens
        state.output_tokens += response.usage.output_tokens
        state.step(
            "llm",
            response.finish_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        state.messages.append(response.message)

        outcome = await _handle(state, response, question, max_validation_retries)
        if outcome is not None:
            return outcome

    return _finish(state, question, Status.STEP_LIMIT, violations=state.last_violations)


async def _handle(
    state: _State, response: LLMResponse, question: str, max_retries: int
) -> AssistantRun | None:
    """Process one model response. Returns the finished run, or None to keep looping."""
    if response.finish_reason == "content_filter":
        return _finish(state, question, Status.REFUSED)
    calls = response.message.tool_calls
    if not calls:  # plain text: never shown
        if state.text_nudges >= 1:
            return _finish(state, question, Status.UNVERIFIED)
        state.text_nudges += 1
        state.messages.append(Message("user", NUDGE))
        return None

    final_calls = [c for c in calls if c.name == FINAL_TOOL]
    for call in calls:
        if call.name == FINAL_TOOL:
            continue
        result = await state.runner.run(call)
        state.messages.append(_reply(call, result.to_model_text()))
        ok = not isinstance(result, ToolError)
        state.step(
            "tool",
            call.name,
            detail=result.id if isinstance(result, Evidence) else result.code,
            ok=ok,
        )

    if not final_calls:
        return None
    first, extras = final_calls[0], final_calls[1:]
    for extra in extras:
        state.messages.append(
            _reply(extra, ToolError("duplicate_final", "Call final_answer once.").to_model_text())
        )
    if len(calls) > len(final_calls):
        state.messages.append(
            _reply(
                first,
                ToolError(
                    "final_answer_too_early",
                    "Call final_answer on its own, after you have read the tool results.",
                ).to_model_text(),
            )
        )
        return None
    return _check_final(state, first, question, max_retries)


def _check_final(
    state: _State, call: ToolCall, question: str, max_retries: int
) -> AssistantRun | None:
    try:
        args = FinalAnswerArgs.model_validate(call.arguments)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in exc.errors()
        )
        state.validation_attempts += 1
        state.step("validation", "schema", detail=problems[:200], ok=False)
        if state.validation_attempts > max_retries:
            return _finish(state, question, Status.UNVERIFIED)
        state.messages.append(
            _reply(call, ToolError("invalid_arguments", problems).to_model_text())
        )
        return None

    draft = DraftAnswer(
        claims=tuple(Claim(c.text, tuple(c.evidence_ids)) for c in args.claims),
        caveats=tuple(args.caveats),
        confidence=args.confidence,
    )
    evidence = state.runner.evidence
    result: ValidationResult = validate_answer(draft, evidence)
    state.step(
        "validation",
        "faithfulness",
        detail=f"{result.numbers_checked} numbers, {result.dates_checked} dates, "
        f"{len(result.violations)} violations",
        ok=result.ok,
    )
    if result.ok:
        return _finish(
            state,
            question,
            Status.ANSWERED,
            claims=draft.claims,
            caveats=draft.caveats,
            confidence=draft.confidence,
        )

    state.validation_attempts += 1
    state.last_violations = result.violations
    if state.validation_attempts <= max_retries:
        state.messages.append(_reply(call, correction_message(result)))
        return None

    kept, caveats = _salvage(draft, evidence)
    if kept:
        return _finish(
            state,
            question,
            Status.PARTIAL,
            claims=kept,
            caveats=caveats,
            confidence="low",
            dropped=len(draft.claims) - len(kept),
            violations=result.violations,
        )
    return _finish(state, question, Status.UNVERIFIED, violations=result.violations)
