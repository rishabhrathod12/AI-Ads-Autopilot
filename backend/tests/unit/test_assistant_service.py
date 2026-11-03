"""AssistantService: history, redaction, validation of input, persistence of every outcome."""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.application.assistant.loop import FINAL_TOOL, Status
from app.application.assistant.service import (
    HISTORY_MESSAGES,
    MAX_QUESTION_CHARS,
    AssistantService,
    InvalidQuestionError,
    ThreadNotFoundError,
)
from app.application.assistant.tools import ToolContext
from app.application.llm.cost import Pricing
from app.application.ports.llm import (
    LLMResponse,
    LLMUnavailableError,
    Message,
    ToolCall,
    ToolSpec,
    Usage,
)
from app.infrastructure.llm.fake import ScriptedLLM, tool_reply
from app.infrastructure.providers.mock.generator import GeneratedData, MockConfig, generate
from tests.unit.fake_reader import FakeReader
from tests.unit.memory_assistant_store import MemoryAssistantStore

END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
ORG = UUID("99999999-9999-4999-8999-999999999999")


class NoForecasts:
    async def save(self, run: Any) -> UUID:
        raise AssertionError

    async def get(self, run_id: UUID) -> None:
        return None

    async def latest(self, *a: Any) -> None:
        return None


@pytest.fixture(scope="module")
def data() -> GeneratedData:
    return generate(MockConfig(end_date=END, now=NOW))


def service(
    data: GeneratedData,
    llm: ScriptedLLM,
    store: MemoryAssistantStore,
    pricing: Pricing | None = None,
) -> AssistantService:
    return AssistantService(
        llm, ToolContext(FakeReader(data, ORG, NOW), NoForecasts()), store, pricing
    )


def answer_from_evidence(messages: Sequence[Message], _tools: Sequence[ToolSpec]) -> LLMResponse:
    ev = next(
        json.loads(m.content or "")
        for m in reversed(messages)
        if m.role == "tool" and '"evidence_id"' in (m.content or "")
    )
    text = f"ACOS was {ev['data']['kpis']['acos_pct']:.1f}% over the last {ev['arguments']['days']} days."
    args = {
        "claims": [{"text": text, "evidence_ids": [ev["evidence_id"]]}],
        "caveats": [],
        "confidence": "high",
    }
    return LLMResponse(
        Message("assistant", None, (ToolCall("f", FINAL_TOOL, args),)),
        Usage(1000, 200),
        "scripted",
        "tool_calls",
    )


def summary() -> LLMResponse:
    return tool_reply(("get_dashboard_summary", {"days": 28}))


async def test_a_turn_is_answered_and_persisted(data: GeneratedData) -> None:
    store = MemoryAssistantStore()
    svc = service(
        data, ScriptedLLM([summary(), answer_from_evidence]), store, Pricing(Decimal(2), Decimal(8))
    )
    thread = await svc.start_thread("My account")
    run, run_id = await svc.ask(thread, "How is my ACOS?")

    assert run.status is Status.ANSWERED
    stored = await store.get_run(run_id)
    assert stored is not None
    assert stored.status == "answered"
    assert stored.question == "How is my ACOS?"
    assert stored.answer["claims"][0]["evidence_ids"] == ["ev_1"]
    assert [e["id"] for e in stored.evidence] == ["ev_1"]
    assert stored.evidence[0]["tool"] == "get_dashboard_summary"
    assert [s["kind"] for s in stored.trace] == ["llm", "tool", "llm", "validation"]
    assert stored.input_tokens == 1100
    assert stored.cost_usd == Pricing(Decimal(2), Decimal(8)).cost(
        Usage(run.input_tokens, run.output_tokens)
    )
    assert stored.llm_calls == 2
    assert stored.tool_calls == 1

    messages = await store.messages(thread)
    assert [(m.role, m.run_id) for m in messages] == [("user", None), ("assistant", run_id)]
    assert messages[1].content == run.answer_text


async def test_without_pricing_the_cost_is_recorded_as_zero_not_guessed(
    data: GeneratedData,
) -> None:
    store = MemoryAssistantStore()
    svc = service(data, ScriptedLLM([summary(), answer_from_evidence]), store)
    _, run_id = await svc.ask(await svc.start_thread("t"), "q")
    stored = await store.get_run(run_id)
    assert stored is not None
    assert stored.cost_usd == 0


async def test_later_turns_see_earlier_ones_as_context(data: GeneratedData) -> None:
    store = MemoryAssistantStore()
    llm = ScriptedLLM([summary(), answer_from_evidence, summary(), answer_from_evidence])
    svc = service(data, llm, store)
    thread = await svc.start_thread("t")
    first, _ = await svc.ask(thread, "How is my ACOS?")
    await svc.ask(thread, "And how about now?")
    second_call_messages = llm.calls[2].messages
    assert [m.role for m in second_call_messages] == ["system", "user", "assistant", "user"]
    assert second_call_messages[1].content == "How is my ACOS?"
    assert second_call_messages[2].content == first.answer_text
    assert second_call_messages[3].content == "And how about now?"


async def test_only_recent_history_is_sent(data: GeneratedData) -> None:
    store = MemoryAssistantStore()
    llm = ScriptedLLM([summary(), answer_from_evidence] * 5)
    svc = service(data, llm, store)
    thread = await svc.start_thread("t")
    for i in range(4):
        await svc.ask(thread, f"question {i}")
    history_in_last = [m for m in llm.calls[-2].messages if m.role in ("user", "assistant")]
    assert len(history_in_last) == HISTORY_MESSAGES + 1  # recent history + the new question


async def test_secrets_and_emails_never_reach_the_model_or_the_database(
    data: GeneratedData,
) -> None:
    store = MemoryAssistantStore()
    llm = ScriptedLLM([summary(), answer_from_evidence])
    svc = service(data, llm, store)
    thread = await svc.start_thread("t")
    _, run_id = await svc.ask(
        thread, "My key is sk-proj-abcdef1234567890 and I am a@b.co, how is ACOS?"
    )
    sent = " ".join(m.content or "" for call in llm.calls for m in call.messages)
    assert "sk-proj" not in sent
    assert "a@b.co" not in sent
    stored = await store.get_run(run_id)
    assert stored is not None
    assert "sk-proj" not in stored.question
    assert "[REDACTED]" in stored.question
    assert all("sk-proj" not in m.content for m in await store.messages(thread))


@pytest.mark.parametrize("question", ["", "   ", "\n\t"])
async def test_empty_questions_are_rejected_before_any_model_call(
    data: GeneratedData, question: str
) -> None:
    llm = ScriptedLLM([])
    svc = service(data, llm, MemoryAssistantStore())
    with pytest.raises(InvalidQuestionError, match="empty"):
        await svc.ask(await svc.start_thread("t"), question)
    assert llm.calls == []


async def test_overlong_questions_are_rejected(data: GeneratedData) -> None:
    llm = ScriptedLLM([])
    svc = service(data, llm, MemoryAssistantStore())
    with pytest.raises(InvalidQuestionError, match="longer than"):
        await svc.ask(await svc.start_thread("t"), "x" * (MAX_QUESTION_CHARS + 1))
    assert llm.calls == []


async def test_an_unknown_thread_is_rejected(data: GeneratedData) -> None:
    llm = ScriptedLLM([])
    with pytest.raises(ThreadNotFoundError):
        await service(data, llm, MemoryAssistantStore()).ask(uuid4(), "hello?")
    assert llm.calls == []


async def test_failures_are_persisted_too_so_the_history_is_complete(data: GeneratedData) -> None:
    store = MemoryAssistantStore()
    svc = service(data, ScriptedLLM([LLMUnavailableError("down")]), store)
    thread = await svc.start_thread("t")
    run, run_id = await svc.ask(thread, "How is my ACOS?")
    assert run.status is Status.LLM_UNAVAILABLE
    stored = await store.get_run(run_id)
    assert stored is not None
    assert stored.status == "llm_unavailable"
    assert stored.model == "none"
    assert stored.llm_calls == 0
    assert (await store.messages(thread))[-1].content == run.answer_text


async def test_thread_titles_are_redacted_and_never_empty(data: GeneratedData) -> None:
    store = MemoryAssistantStore()
    svc = service(data, ScriptedLLM([]), store)
    secret = await svc.start_thread("key sk-abcdefgh12345678")
    blank = await svc.start_thread("   ")
    assert "sk-abcdefgh" not in store.threads[secret]
    assert store.threads[blank] == "Conversation"


async def test_the_graph_engine_answers_and_persists_the_same_way(data: GeneratedData) -> None:
    plan = LLMResponse(
        Message("assistant", '{"specialists":["campaign_analysis"],"rationale":"kpis"}'),
        Usage(100, 20),
        "scripted",
        "stop",
    )
    done = LLMResponse(Message("assistant", "done"), Usage(100, 2), "scripted", "stop")
    store = MemoryAssistantStore()
    svc = AssistantService(
        ScriptedLLM([plan, summary(), done, answer_from_evidence]),
        ToolContext(FakeReader(data, ORG, NOW), NoForecasts()),
        store,
        Pricing(Decimal(2), Decimal(8)),
        engine="graph",
    )
    thread = await svc.start_thread("My account")
    run, run_id = await svc.ask(thread, "How is my ACOS?")

    assert run.status is Status.ANSWERED
    stored = await store.get_run(run_id)
    assert stored is not None
    assert [e["id"] for e in stored.evidence] == ["ev_1"]
    assert next(s["name"] for s in stored.trace if s["kind"] == "llm") == "supervisor.plan"
