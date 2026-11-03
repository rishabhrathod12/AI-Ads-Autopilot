"""Assistant persistence on PostgreSQL: trace storage, immutability, isolation, spend ledger."""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.application.assistant.loop import FINAL_TOOL, AssistantRun, Status, run_assistant
from app.application.assistant.service import AssistantService
from app.application.assistant.tools import ToolContext
from app.application.llm.budget import BudgetedLLMClient, SpendLimits
from app.application.llm.cost import Pricing
from app.application.ports.llm import (
    LLMBudgetExceededError,
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
    Usage,
)
from app.domain.tenant import TenantContext
from app.infrastructure.db import assistant_models  # noqa: F401
from app.infrastructure.db.assistant_store import SqlAssistantStore, SqlSpendLedger
from app.infrastructure.db.models import Organization
from app.infrastructure.db.session import tenant_session
from app.infrastructure.llm.fake import ScriptedLLM, tool_reply
from app.infrastructure.providers.mock.generator import MockConfig, generate
from tests.unit.fake_reader import FakeReader

pytestmark = pytest.mark.db
ROLE = "adpilot_app"
END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
DATA = generate(
    MockConfig(
        days=100,
        end_date=END,
        n_campaigns=6,
        ad_groups_per_campaign=2,
        keywords_per_ad_group=10,
        now=NOW,
    )
)
PRICING = Pricing(Decimal(2), Decimal(8))


class NoForecasts:
    async def save(self, run: Any) -> UUID:
        raise AssertionError

    async def get(self, run_id: UUID) -> None:
        return None

    async def latest(self, *a: Any) -> None:
        return None


def answer(messages: Sequence[Message], _tools: Sequence[ToolSpec]) -> LLMResponse:
    ev = next(
        json.loads(m.content or "")
        for m in reversed(messages)
        if m.role == "tool" and '"evidence_id"' in (m.content or "")
    )
    text_ = f"ACOS was {ev['data']['kpis']['acos_pct']:.1f}%."
    args = {
        "claims": [{"text": text_, "evidence_ids": [ev["evidence_id"]]}],
        "caveats": [],
        "confidence": "high",
    }
    return LLMResponse(
        Message("assistant", None, (ToolCall("f", FINAL_TOOL, args),)),
        Usage(1000, 200),
        "scripted",
        "tool_calls",
    )


def script() -> list[Any]:
    return [tool_reply(("get_dashboard_summary", {"days": 28})), answer]


async def new_org(engine: AsyncEngine) -> UUID:
    org = uuid4()
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        s.add(Organization(id=org, name="Assistant test"))
    return org


async def real_run(org: UUID) -> AssistantRun:
    return await run_assistant(
        ScriptedLLM(script()),
        ToolContext(FakeReader(DATA, org, NOW), NoForecasts()),
        "How is my ACOS?",
    )


async def test_a_turn_round_trips_with_its_evidence_and_trace(engine: AsyncEngine) -> None:
    org = await new_org(engine)
    run = await real_run(org)
    assert run.status is Status.ANSWERED
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        store = SqlAssistantStore(s, org)
        thread = await store.create_thread("Account review")
        run_id = await store.save_turn(thread, run, Decimal("0.003600"))
        got = await store.get_run(run_id)
        msgs = await store.messages(thread)
        listed = await store.runs(thread)
    assert got is not None
    assert (got.status, got.question, got.model) == ("answered", "How is my ACOS?", "scripted")
    assert got.cost_usd == Decimal("0.003600")
    assert (got.input_tokens, got.output_tokens, got.llm_calls, got.tool_calls) == (
        run.input_tokens,
        run.output_tokens,
        2,
        1,
    )
    assert got.answer["claims"][0]["evidence_ids"] == ["ev_1"]
    assert got.answer["text"] == run.answer_text
    assert got.evidence[0]["tool"] == "get_dashboard_summary"
    assert got.evidence[0]["synthetic"] is True
    assert (
        got.evidence[0]["data"]["totals"]["spend_paise"]
        == run.evidence[0].data["totals"]["spend_paise"]
    )
    assert [t["kind"] for t in got.trace] == ["llm", "tool", "llm", "validation"]
    assert [(m.role, m.run_id) for m in msgs] == [("user", None), ("assistant", run_id)]
    assert msgs[0].created_at <= msgs[1].created_at
    assert [r.id for r in listed] == [run_id]


async def test_messages_come_back_oldest_first_and_limited_to_the_most_recent(
    engine: AsyncEngine,
) -> None:
    org = await new_org(engine)
    run = await real_run(org)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        store = SqlAssistantStore(s, org)
        thread = await store.create_thread("t")
        for _ in range(3):
            await store.save_turn(thread, run, Decimal(0))
        everything = await store.messages(thread, limit=50)
        recent = await store.messages(thread, limit=4)
    assert [m.role for m in everything] == ["user", "assistant"] * 3
    assert [m.id for m in recent] == [m.id for m in everything[-4:]]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE assistant_threads SET title = 'x'",
        "DELETE FROM assistant_threads",
        "UPDATE assistant_runs SET status = 'answered'",
        "DELETE FROM assistant_runs",
        "UPDATE assistant_messages SET content = 'x'",
        "DELETE FROM assistant_messages",
        "UPDATE llm_usage SET cost_usd = 0",
        "DELETE FROM llm_usage",
    ],
)
async def test_conversation_history_is_immutable(engine: AsyncEngine, statement: str) -> None:
    org = await new_org(engine)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        store = SqlAssistantStore(s, org)
        thread = await store.create_thread("t")
        await store.save_turn(thread, await real_run(org), Decimal(0))
        await SqlSpendLedger(s, org).record(org, Decimal("0.01"), "m", NOW)
    with pytest.raises(ProgrammingError):
        async with tenant_session(engine, TenantContext(org), ROLE) as s:
            await s.execute(text(statement))


async def test_tenants_cannot_see_or_touch_each_others_conversations(engine: AsyncEngine) -> None:
    org_a, org_b = await new_org(engine), await new_org(engine)
    async with tenant_session(engine, TenantContext(org_a), ROLE) as s:
        store = SqlAssistantStore(s, org_a)
        thread = await store.create_thread("private")
        run_id = await store.save_turn(thread, await real_run(org_a), Decimal(0))
        await SqlSpendLedger(s, org_a).record(org_a, Decimal("1"), "m", NOW)
    async with tenant_session(engine, TenantContext(org_b), ROLE) as s:
        other = SqlAssistantStore(s, org_b)
        assert not await other.thread_exists(thread)
        assert await other.messages(thread) == []
        assert await other.get_run(run_id) is None
        assert await other.runs(thread) == []
        assert not await SqlAssistantStore(s, org_a).thread_exists(thread)  # RLS holds when asked
        assert await SqlSpendLedger(s, org_b).spent_since(org_a, NOW - timedelta(days=1)) == 0
    with pytest.raises(DBAPIError):  # WITH CHECK blocks writing into another tenant
        async with tenant_session(engine, TenantContext(org_b), ROLE) as s:
            await SqlAssistantStore(s, org_a).create_thread("intruder")


async def test_the_database_rejects_impossible_records(engine: AsyncEngine) -> None:
    org = await new_org(engine)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        thread = await SqlAssistantStore(s, org).create_thread("t")
    bad_inserts = [
        (
            "INSERT INTO assistant_messages (id, organization_id, thread_id, role, content) "
            "VALUES (gen_random_uuid(), :org, :thread, 'system', 'x')",  # role must be user/assistant
            {"org": org, "thread": thread},
        ),
        (
            "INSERT INTO llm_usage (id, organization_id, model, cost_usd) "
            "VALUES (gen_random_uuid(), :org, 'm', -1)",  # spend cannot be negative
            {"org": org},
        ),
    ]
    for statement, params in bad_inserts:
        with pytest.raises(DBAPIError):
            async with tenant_session(engine, TenantContext(org), ROLE) as s:
                await s.execute(text(statement), params)


async def test_the_spend_ledger_sums_by_window_and_tenant(engine: AsyncEngine) -> None:
    org, other = await new_org(engine), await new_org(engine)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        ledger = SqlSpendLedger(s, org)
        await ledger.record(org, Decimal("0.25"), "m", NOW - timedelta(days=3))
        await ledger.record(org, Decimal("0.5"), "m", NOW)
        await ledger.record(org, Decimal("0.125"), "m", NOW + timedelta(hours=1))
        assert await ledger.spent_since(org, NOW - timedelta(days=10)) == Decimal("0.875")
        assert await ledger.spent_since(org, NOW - timedelta(days=1)) == Decimal("0.625")
        assert await ledger.spent_since(org, NOW + timedelta(days=1)) == 0
    async with tenant_session(engine, TenantContext(other), ROLE) as s:
        assert await SqlSpendLedger(s, other).spent_since(other, NOW - timedelta(days=10)) == 0


async def test_the_spend_cap_holds_across_requests_through_the_database(
    engine: AsyncEngine,
) -> None:
    org = await new_org(engine)
    limits = SpendLimits(Decimal("0.01"), Decimal("1"))

    async def one_request(session: AsyncSession) -> None:
        client = BudgetedLLMClient(
            ScriptedLLM(script()),
            SqlSpendLedger(session, org),
            org,
            limits,
            PRICING,
            clock=lambda: NOW,
        )
        await client.complete([Message("user", "hi")], max_output_tokens=100)

    async with tenant_session(
        engine, TenantContext(org), ROLE
    ) as s:  # request 1: allowed, recorded
        await one_request(s)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        used = await SqlSpendLedger(s, org).spent_since(org, NOW - timedelta(hours=1))
    assert used == PRICING.cost(Usage(100, 20))  # what the scripted reply reported

    async with tenant_session(
        engine, TenantContext(org), ROLE
    ) as s:  # request 2: still under the cap
        await SqlSpendLedger(s, org).record(org, Decimal("0.0095"), "m", NOW)
    with pytest.raises(LLMBudgetExceededError):  # request 3: refused before any model call
        async with tenant_session(engine, TenantContext(org), ROLE) as s:
            await one_request(s)


async def test_the_whole_stack_with_real_storage_and_a_database_ledger(engine: AsyncEngine) -> None:
    org = await new_org(engine)
    llm_script = [*script(), *script()]
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        inner = ScriptedLLM(llm_script)
        llm = BudgetedLLMClient(
            inner,
            SqlSpendLedger(s, org),
            org,
            SpendLimits(Decimal(5), Decimal(50)),
            PRICING,
            clock=lambda: datetime.now(UTC),
        )
        svc = AssistantService(
            llm,
            ToolContext(FakeReader(DATA, org, NOW), NoForecasts()),
            SqlAssistantStore(s, org),
            PRICING,
        )
        thread = await svc.start_thread("Weekly review")
        first, first_id = await svc.ask(thread, "How is my ACOS?")
        second, second_id = await svc.ask(thread, "And now?")
    assert first.status is second.status is Status.ANSWERED
    assert [m.role for m in inner.calls[2].messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]  # history carried
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        store = SqlAssistantStore(s, org)
        stored = [await store.get_run(first_id), await store.get_run(second_id)]
        total = await SqlSpendLedger(s, org).spent_since(
            org, datetime.now(UTC) - timedelta(hours=1)
        )
        count = (await s.execute(text("SELECT count(*) FROM llm_usage"))).scalar_one()
    assert all(r is not None for r in stored)
    assert total == sum((r.cost_usd for r in stored if r), Decimal(0))  # ledger and runs agree
    assert count == first.llm_calls + second.llm_calls
