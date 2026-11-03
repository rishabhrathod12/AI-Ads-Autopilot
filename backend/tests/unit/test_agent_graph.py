"""The M5 graph, driven by a scripted model. No network, no cost."""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest

from app.application.agents.graph import AgentLimits, AgentResult, Plan, plan_schema, run_agents
from app.application.agents.specialists import SPECIALIST_NAMES, SPECIALISTS
from app.application.assistant.loop import FINAL_TOOL, Status
from app.application.assistant.tools import REGISTRY, ToolContext
from app.application.ports.llm import (
    LLMResponse,
    LLMUnavailableError,
    Message,
    ToolSpec,
)
from app.infrastructure.llm.fake import ScriptedLLM, text_reply, tool_reply
from app.infrastructure.providers.mock.generator import GeneratedData, MockConfig, generate
from tests.unit.fake_reader import FakeReader
from tests.unit.test_assistant_loop import NoForecasts, evidence_of, final

END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
ORG = UUID("88888888-8888-4888-8888-888888888888")


@pytest.fixture(scope="module")
def data() -> GeneratedData:
    return generate(MockConfig(end_date=END, now=NOW))


def plan_reply(*names: str) -> LLMResponse:
    body = Plan(specialists=list(names), rationale="test").model_dump_json()
    return text_reply(body)


def done() -> LLMResponse:
    return text_reply("done")


def faithful_final(messages: Sequence[Message], _tools: Sequence[ToolSpec]) -> LLMResponse:
    ev = evidence_of(messages, 0)
    spend = ev["data"]["totals"]["spend_rupees"]
    return final((f"Ad spend was ₹{spend:,.0f}.", [ev["evidence_id"]]))


async def ask(script: list[Any], data: GeneratedData, **kw: Any) -> tuple[AgentResult, ScriptedLLM]:
    llm = ScriptedLLM(script)
    ctx = ToolContext(FakeReader(data, ORG, NOW), NoForecasts())
    return await run_agents(llm, ctx, "How is my account doing?", **kw), llm


def test_specialist_tools_exist_and_are_read_only() -> None:
    for spec in SPECIALISTS.values():
        assert set(spec.tools) <= set(REGISTRY)
    assert set(SPECIALISTS) == set(SPECIALIST_NAMES)
    assert {t for s in SPECIALISTS.values() for t in s.tools} == set(REGISTRY)


def test_plan_schema_is_strict_and_closed() -> None:
    schema = plan_schema().schema
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"specialists", "rationale"}


async def test_route_gather_synthesize_validate(data: GeneratedData) -> None:
    script = [
        plan_reply("campaign_analysis"),
        tool_reply(("get_dashboard_summary", {"days": 28})),
        done(),
        faithful_final,
    ]
    result, llm = await ask(script, data)
    run = result.run
    assert run.status is Status.ANSWERED
    assert result.plan.specialists == ["campaign_analysis"]
    assert not result.plan_fallback
    assert run.cited == {"ev_1"}
    assert run.llm_calls == 4
    assert llm.remaining == 0
    assert [s.name for s in run.steps if s.kind == "llm"] == [
        "supervisor.plan",
        "campaign_analysis.llm",
        "campaign_analysis.llm",
        "synthesizer",
    ]
    assert llm.calls[0].response_schema is not None


async def test_specialist_only_sees_its_own_tools(data: GeneratedData) -> None:
    _, llm = await ask([plan_reply("keyword_intelligence"), done(), final(("No data.", []))], data)
    offered = {t.name for t in llm.calls[1].tools}
    assert offered == {"get_keyword_findings", "get_top_keywords"}


async def test_a_tool_outside_the_specialists_list_is_refused(data: GeneratedData) -> None:
    script = [
        plan_reply("keyword_intelligence"),
        tool_reply(("get_dashboard_summary", {"days": 28})),
        done(),
        final(("I could not get the data.", [])),
    ]
    result, _ = await ask(script, data)
    assert result.run.evidence == ()
    assert result.run.tool_calls == 0


async def test_empty_plan_goes_straight_to_the_synthesizer(data: GeneratedData) -> None:
    script = [plan_reply(), final(("I cannot make changes to your campaigns.", []))]
    result, llm = await ask(script, data)
    assert result.run.status is Status.ANSWERED
    assert result.run.llm_calls == 2
    assert [t.name for t in llm.calls[1].tools] == [FINAL_TOOL]


async def test_synthesizer_gets_evidence_as_tool_messages(data: GeneratedData) -> None:
    script = [
        plan_reply("campaign_analysis"),
        tool_reply(("get_dashboard_summary", {"days": 28})),
        done(),
        faithful_final,
    ]
    _, llm = await ask(script, data)
    synth = llm.calls[-1].messages
    assert [m.role for m in synth] == ["system", "user", "assistant", "tool"]
    assert synth[2].tool_calls[0].id == synth[3].tool_call_id


async def test_unusable_plan_runs_every_specialist(data: GeneratedData) -> None:
    script = [text_reply("not json"), done(), done(), done(), final(("Nothing found.", []))]
    result, _ = await ask(script, data)
    assert result.plan_fallback
    assert result.plan.specialists == list(SPECIALIST_NAMES)
    assert result.run.status is Status.ANSWERED


async def test_a_wrong_number_is_corrected_once_by_the_validator(data: GeneratedData) -> None:
    def wrong(messages: Sequence[Message], _t: Sequence[ToolSpec]) -> LLMResponse:
        return final(("Ad spend was ₹1,234,567.", [evidence_of(messages, 0)["evidence_id"]]))

    script = [
        plan_reply("campaign_analysis"),
        tool_reply(("get_dashboard_summary", {"days": 28})),
        done(),
        wrong,
        faithful_final,
    ]
    result, _ = await ask(script, data)
    assert result.run.status is Status.ANSWERED
    assert result.run.validation_attempts == 1


async def test_a_wrong_number_that_persists_is_dropped_not_shown(data: GeneratedData) -> None:
    def wrong(messages: Sequence[Message], _t: Sequence[ToolSpec]) -> LLMResponse:
        return final(("Ad spend was ₹1,234,567.", [evidence_of(messages, 0)["evidence_id"]]))

    script = [
        plan_reply("campaign_analysis"),
        tool_reply(("get_dashboard_summary", {"days": 28})),
        done(),
        wrong,
        wrong,
    ]
    result, _ = await ask(script, data)
    assert result.run.status is Status.UNVERIFIED
    assert "1,234,567" not in result.run.answer_text


async def test_plain_text_from_the_synthesizer_is_never_shown(data: GeneratedData) -> None:
    script = [plan_reply(), text_reply("Spend is ₹9."), text_reply("Spend is ₹9.")]
    result, _ = await ask(script, data)
    assert result.run.status is Status.UNVERIFIED
    assert "₹9" not in result.run.answer_text


async def test_llm_outage_in_the_supervisor_ends_cleanly(data: GeneratedData) -> None:
    result, _ = await ask([LLMUnavailableError("down")], data)
    assert result.run.status is Status.LLM_UNAVAILABLE


async def test_llm_call_budget_is_enforced(data: GeneratedData) -> None:
    looping = tool_reply(("get_dashboard_summary", {"days": 28}))
    result, _ = await ask(
        [plan_reply("campaign_analysis"), looping, looping, looping],
        data,
        limits=AgentLimits(max_llm_calls=3),
    )
    assert result.run.status is Status.STEP_LIMIT
    assert result.run.llm_calls == 3


async def test_two_specialists_share_evidence_ids_and_the_call_cache(data: GeneratedData) -> None:
    same = tool_reply(("get_anomalies", {}))
    script = [
        plan_reply("campaign_analysis", "performance_prediction"),
        same,
        done(),
        tool_reply(("get_account_forecast", {})),
        done(),
        final(("Checked anomalies and the forecast.", ["ev_1", "ev_2"])),
    ]
    result, _ = await ask(script, data)
    assert [e.id for e in result.run.evidence] == ["ev_1", "ev_2"]
    assert result.run.status is Status.ANSWERED


async def test_token_usage_is_summed_across_nodes(data: GeneratedData) -> None:
    result, _ = await ask([plan_reply(), final(("I cannot make changes.", []))], data)
    assert result.run.input_tokens == 200
