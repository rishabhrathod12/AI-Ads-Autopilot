"""The M5 graph: supervisor -> specialists (one at a time) -> synthesizer <-> validator.

* Specialists run sequentially because they share one database session and one ``ToolRunner``,
  so evidence ids are unique and identical calls are cached across specialists.
* The validator is the M4 faithfulness check, unchanged (ADR 0015). One correction round, then
  unsupported claims are dropped, exactly as in the single assistant.
* The result is the same ``AssistantRun`` the single assistant returns, so storage and the API
  are unchanged. Counters and evidence for a run live on ``GraphRun``; moving them into
  checkpointed state is the checkpointing step.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.application.agents.prompts import (
    NUDGE,
    SUPERVISOR_PROMPT,
    SYNTHESIZER_PROMPT,
    specialist_prompt,
)
from app.application.agents.specialists import SPECIALIST_NAMES, SPECIALISTS, SpecialistName
from app.application.assistant.evidence import Evidence, ToolError
from app.application.assistant.loop import (
    FINAL_TOOL,
    AssistantRun,
    RunState,
    Status,
    check_final,
    final_answer_spec,
    finish_run,
    reply_message,
)
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
    ResponseSchema,
    ToolCall,
    ToolSpec,
)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialists: list[SpecialistName] = Field(max_length=3)
    rationale: str = Field(max_length=300)


def plan_schema() -> ResponseSchema:
    schema = Plan.model_json_schema()
    schema.pop("title", None)
    schema["additionalProperties"] = False
    for prop in schema.get("properties", {}).values():
        for key in ("maxLength", "maxItems", "title"):
            prop.pop(key, None)
    return ResponseSchema("routing_plan", schema)


@dataclass(frozen=True)
class AgentLimits:
    max_llm_calls: int = 14
    max_tool_calls: int = 10
    max_specialist_llm_calls: int = 3
    max_validation_retries: int = 1
    max_output_tokens: int = 1500


@dataclass(frozen=True)
class AgentResult:
    run: AssistantRun
    plan: Plan
    plan_fallback: bool  # the supervisor's plan was unusable, so every specialist ran


class GraphState(TypedDict, total=False):
    pending: list[str]


def _failure_status(exc: Exception) -> Status | None:
    if isinstance(exc, LLMBudgetExceededError):
        return Status.BUDGET_EXCEEDED
    if isinstance(exc, LLMNotConfiguredError):
        return Status.NOT_CONFIGURED
    if isinstance(exc, LLMUnavailableError | LLMRateLimitedError):
        return Status.LLM_UNAVAILABLE
    if isinstance(exc, LLMAuthError | LLMBadRequestError | LLMResponseError):
        return Status.LLM_ERROR
    return None


def _error_reply(call: ToolCall, code: str, message: str) -> Message:
    return reply_message(call, ToolError(code, message).to_model_text())


class GraphRun:
    """One question's run. Build per request: it holds that request's evidence and counters."""

    def __init__(
        self,
        llm: LLMClient,
        ctx: ToolContext,
        question: str,
        history: Sequence[tuple[Literal["user", "assistant"], str]],
        limits: AgentLimits,
    ) -> None:
        self.llm = llm
        self.question = question
        self.history = [Message(role, text) for role, text in history]
        self.limits = limits
        self.knowledge = ctx.has_knowledge
        self.state = RunState([], ToolRunner(ctx, max_calls=limits.max_tool_calls))
        self.gathered: list[tuple[ToolCall, Evidence | ToolError]] = []
        self.plan = Plan(specialists=[], rationale="")
        self.plan_fallback = False
        self.result: AssistantRun | None = None
        self._nudged = False
        self._final_call: ToolCall | None = None

    # -- helpers -------------------------------------------------------------------------------

    def _stop(self, status: Status) -> None:
        self.result = finish_run(self.state, self.question, status)

    async def _llm(
        self, name: str, messages: Sequence[Message], **kwargs: Any
    ) -> LLMResponse | None:
        """One bounded model call. On failure the run is finished and None is returned."""
        if self.state.llm_calls >= self.limits.max_llm_calls:
            self._stop(Status.STEP_LIMIT)
            return None
        started = time.monotonic()
        try:
            response = await self.llm.complete(
                messages, max_output_tokens=self.limits.max_output_tokens, temperature=0.0, **kwargs
            )
        except Exception as exc:
            status = _failure_status(exc)
            if status is None:
                raise
            self._stop(status)
            return None
        st = self.state
        st.llm_calls += 1
        st.model = response.model or st.model
        st.input_tokens += response.usage.input_tokens
        st.output_tokens += response.usage.output_tokens
        st.step(
            "llm",
            name,
            detail=response.finish_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return response

    # -- nodes ---------------------------------------------------------------------------------

    async def plan_node(self, state: GraphState) -> GraphState:
        messages = [
            Message("system", SUPERVISOR_PROMPT),
            *self.history,
            Message("user", self.question),
        ]
        response = await self._llm("supervisor.plan", messages, response_schema=plan_schema())
        if response is None:
            return {}
        try:
            plan = Plan.model_validate_json(response.message.content or "")
        except ValidationError:
            # An unusable plan must not lose the question: run everything, and say so in the trace.
            self.plan_fallback = True
            plan = Plan(specialists=list(SPECIALIST_NAMES), rationale="fallback: plan unusable")
            self.state.step("llm", "supervisor.plan_fallback", ok=False)
        plan = plan.model_copy(update={"specialists": list(dict.fromkeys(plan.specialists))})
        self.plan = plan
        return {"pending": list(plan.specialists)}

    async def specialist_node(self, state: GraphState) -> GraphState:
        pending = list(state.get("pending", []))
        spec = SPECIALISTS[pending.pop(0)]
        allowed = {t for t in spec.tools if t != "search_knowledge" or self.knowledge}
        tools: list[ToolSpec] = [
            t for t in tool_specs(knowledge=self.knowledge) if t.name in allowed
        ]
        messages = [
            Message("system", specialist_prompt(spec)),
            *self.history,
            Message("user", self.question),
        ]
        for _ in range(self.limits.max_specialist_llm_calls):
            response = await self._llm(f"{spec.name}.llm", messages, tools=tools)
            if response is None:
                return {"pending": []}
            messages.append(response.message)
            calls = response.message.tool_calls
            if not calls:
                break
            for call in calls:
                if call.name not in allowed:
                    outcome: Evidence | ToolError = ToolError(
                        "unknown_tool", f"There is no tool named {call.name!r}."
                    )
                else:
                    outcome = await self.state.runner.run(call)
                messages.append(reply_message(call, outcome.to_model_text()))
                self.gathered.append((call, outcome))
                self.state.step(
                    "tool",
                    f"{spec.name}:{call.name}",
                    detail=outcome.id if isinstance(outcome, Evidence) else outcome.code,
                    ok=isinstance(outcome, Evidence),
                )
        return {"pending": pending}

    def _evidence_messages(self) -> list[Message]:
        """Gathered results as tool-role messages, so seller text never sits in a user message."""
        if not self.gathered:
            return []
        calls = tuple(
            ToolCall(f"gathered_{i}", c.name, c.arguments) for i, (c, _) in enumerate(self.gathered)
        )
        out = [Message("assistant", None, calls)]
        out += [
            Message("tool", outcome.to_model_text(), tool_call_id=f"gathered_{i}")
            for i, (_, outcome) in enumerate(self.gathered)
        ]
        return out

    async def synthesize_node(self, state: GraphState) -> GraphState:
        st = self.state
        self._final_call = None
        if not st.messages:
            st.messages = [
                Message("system", SYNTHESIZER_PROMPT),
                *self.history,
                Message("user", self.question),
                *self._evidence_messages(),
            ]
        response = await self._llm("synthesizer", st.messages, tools=[final_answer_spec()])
        if response is None:
            return {}
        if response.finish_reason == "content_filter":
            self._stop(Status.REFUSED)
            return {}
        st.messages.append(response.message)
        calls = response.message.tool_calls
        finals = [c for c in calls if c.name == FINAL_TOOL]
        if not finals:
            for call in calls:  # no data tools exist here, but every call still needs a reply
                st.messages.append(_error_reply(call, "unknown_tool", "Only final_answer exists."))
            if self._nudged:
                self._stop(Status.UNVERIFIED)
            else:
                self._nudged = True
                st.messages.append(Message("user", NUDGE))
            return {}
        if len(calls) > 1:  # final_answer must stand alone; answer every call and ask again
            for call in calls:
                st.messages.append(
                    _error_reply(call, "not_allowed", "Call final_answer once, on its own.")
                )
            return {}
        self._final_call = finals[0]
        return {}

    def validate_node(self, state: GraphState) -> GraphState:
        call, self._final_call = self._final_call, None
        if call is not None:
            self.result = check_final(
                self.state, call, self.question, self.limits.max_validation_retries
            )
        return {}

    # -- routing -------------------------------------------------------------------------------

    def next_step(self, graph_state: GraphState) -> str:
        if self.result is not None:
            return END
        return "specialist" if graph_state.get("pending") else "synthesize"

    def after_synthesize(self, state: GraphState) -> str:
        if self.result is not None:
            return END
        return "validate" if self._final_call is not None else "synthesize"

    def after_validate(self, state: GraphState) -> str:
        return END if self.result is not None else "synthesize"

    def build(self) -> Any:
        g = StateGraph(GraphState)
        g.add_node("plan", self.plan_node)
        g.add_node("specialist", self.specialist_node)
        g.add_node("synthesize", self.synthesize_node)
        g.add_node("validate", self.validate_node)
        g.add_edge(START, "plan")
        g.add_conditional_edges("plan", self.next_step)
        g.add_conditional_edges("specialist", self.next_step)
        g.add_conditional_edges("synthesize", self.after_synthesize)
        g.add_conditional_edges("validate", self.after_validate)
        return g.compile()


async def run_agents(
    llm: LLMClient,
    ctx: ToolContext,
    question: str,
    *,
    history: Sequence[tuple[Literal["user", "assistant"], str]] = (),
    limits: AgentLimits | None = None,
) -> AgentResult:
    run = GraphRun(llm, ctx, question, history, limits or AgentLimits())
    await run.build().ainvoke({})
    result = run.result
    if result is None:  # cannot happen: every path to END sets a result
        result = finish_run(run.state, question, Status.LLM_ERROR)
    return AgentResult(result, run.plan, run.plan_fallback)
