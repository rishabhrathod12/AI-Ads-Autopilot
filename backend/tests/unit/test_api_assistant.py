"""The assistant HTTP API: contracts, tenant guard, limits, and what the views expose."""

import json
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.ratelimit import RateLimiter
from app.api.routers.assistant import AssistantDeps, get_assistant
from app.api.tenancy import DEV_ORG_HEADER
from app.application.assistant.loop import FINAL_TOOL
from app.application.assistant.service import AssistantService
from app.application.assistant.tools import ToolContext
from app.application.llm.cost import Pricing
from app.application.ports.llm import LLMResponse, Message, ToolCall, ToolSpec, Usage
from app.config import Settings
from app.infrastructure.llm.fake import ScriptedLLM, tool_reply
from app.infrastructure.providers.mock.generator import MockConfig, generate
from tests.unit.fake_reader import FakeReader
from tests.unit.memory_assistant_store import MemoryAssistantStore
from tests.unit.test_assistant_tools import MemoryStore

END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
ORG = UUID("99999999-9999-4999-8999-999999999999")
H = {DEV_ORG_HEADER: str(ORG)}
DATA = generate(MockConfig(days=60, end_date=END, n_campaigns=6, now=NOW))


def answer(messages: Sequence[Message], _tools: Sequence[ToolSpec]) -> LLMResponse:
    ev = next(
        json.loads(m.content or "")
        for m in reversed(messages)
        if m.role == "tool" and '"evidence_id"' in (m.content or "")
    )
    text = f"ACOS was {ev['data']['kpis']['acos_pct']:.1f}%."
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


def good_script() -> list[Any]:
    return [tool_reply(("get_dashboard_summary", {"days": 28})), answer]


@pytest.fixture
def store() -> MemoryAssistantStore:
    return MemoryAssistantStore()


def make_client(
    store: MemoryAssistantStore,
    script: Callable[[], list[Any]] = good_script,
    *,
    configured: bool = True,
    settings: Settings | None = None,
) -> TestClient:
    app = create_app(settings or Settings(_env_file=None))

    async def deps() -> AsyncIterator[AssistantDeps]:
        def make() -> AssistantService:
            if not configured:
                raise HTTPException(503, "The assistant is not configured.")
            ctx = ToolContext(FakeReader(DATA, ORG, NOW), MemoryStore())
            return AssistantService(
                ScriptedLLM(script()), ctx, store, Pricing(Decimal(2), Decimal(8))
            )

        yield AssistantDeps(ORG, store, make)

    app.dependency_overrides[get_assistant] = deps
    return TestClient(app)


@pytest.fixture
def client(store: MemoryAssistantStore) -> Iterator[TestClient]:
    with make_client(store) as c:
        yield c


def new_thread(c: TestClient) -> str:
    r = c.post("/api/v1/assistant/threads", json={"title": "Review"}, headers=H)
    assert r.status_code == 201, r.text
    return str(r.json()["thread_id"])


def ask(c: TestClient, thread: str, question: str = "How is my ACOS?") -> Any:
    return c.post(
        f"/api/v1/assistant/threads/{thread}/messages", json={"question": question}, headers=H
    )


# ---- guard ------------------------------------------------------------------------------------
def test_every_route_requires_a_tenant_and_a_database_when_not_overridden() -> None:
    with TestClient(create_app(Settings(_env_file=None))) as c:
        thread = uuid4()
        calls = [
            c.post("/api/v1/assistant/threads"),
            c.get(f"/api/v1/assistant/threads/{thread}"),
            c.post(f"/api/v1/assistant/threads/{thread}/messages", json={"question": "hi"}),
            c.get(f"/api/v1/assistant/runs/{thread}"),
        ]
        assert [r.status_code for r in calls] == [401, 401, 401, 401]
        with_header = c.post("/api/v1/assistant/threads", headers=H)
        assert with_header.status_code == 503  # no database: never a silent success


# ---- the happy path -----------------------------------------------------------------------------
def test_asking_returns_a_verified_answer_with_its_data_used(client: TestClient) -> None:
    thread = new_thread(client)
    r = ask(client, thread)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "answered"
    assert body["answer"]["claims"][0]["evidence_ids"] == ["ev_1"]
    chip = body["data_used"][0]
    assert chip["tool"] == "get_dashboard_summary"
    assert chip["synthetic"] is True and chip["data_sources"] == ["MOCK"]
    assert "data" not in chip  # the chip is a label; the payload lives in the audit view
    assert any("MOCK" in n for n in body["answer"]["system_notes"])
    assert Decimal(body["usage"]["cost_usd"]) > 0
    assert "evidence" not in body and "trace" not in body


def test_the_audit_view_exposes_evidence_trace_and_nothing_secret(client: TestClient) -> None:
    run_id = ask(client, new_thread(client)).json()["run_id"]
    r = client.get(f"/api/v1/assistant/runs/{run_id}", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["evidence"][0]["data"]["kpis"]["acos_pct"] is not None
    assert [s["kind"] for s in body["trace"]] == ["llm", "tool", "llm", "validation"]
    assert "sk-" not in r.text and "system prompt" not in r.text.lower()


def test_a_thread_returns_its_messages_oldest_first_and_its_runs(client: TestClient) -> None:
    thread = new_thread(client)
    ask(client, thread)
    r = client.get(f"/api/v1/assistant/threads/{thread}", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert len(body["runs"]) == 1


# ---- errors -----------------------------------------------------------------------------------
@pytest.mark.parametrize("question", ["", "x" * 2001])
def test_empty_and_oversized_questions_are_rejected(client: TestClient, question: str) -> None:
    assert ask(client, new_thread(client), question).status_code == 422


def test_unknown_fields_and_unknown_ids_are_rejected(client: TestClient) -> None:
    thread = new_thread(client)
    extra = client.post(
        f"/api/v1/assistant/threads/{thread}/messages",
        json={"question": "hi", "organization_id": str(uuid4())},
        headers=H,
    )
    assert extra.status_code == 422  # a caller cannot name a tenant
    assert ask(client, str(uuid4())).status_code == 404
    assert client.get(f"/api/v1/assistant/threads/{uuid4()}", headers=H).status_code == 404
    assert client.get(f"/api/v1/assistant/runs/{uuid4()}", headers=H).status_code == 404
    assert client.get("/api/v1/assistant/runs/not-a-uuid", headers=H).status_code == 422


def test_an_unconfigured_assistant_is_503_but_stored_answers_stay_readable(
    store: MemoryAssistantStore,
) -> None:
    with make_client(store) as ok:
        thread = new_thread(ok)
        run_id = ask(ok, thread).json()["run_id"]
    with make_client(store, configured=False) as off:
        assert ask(off, thread).status_code == 503
        assert off.get(f"/api/v1/assistant/runs/{run_id}", headers=H).status_code == 200


def test_a_failed_model_call_is_reported_in_the_body_not_hidden(
    store: MemoryAssistantStore,
) -> None:
    from app.application.ports.llm import LLMUnavailableError

    def script() -> list[Any]:
        return [LLMUnavailableError("down")] * 6

    with make_client(store, script) as c:
        r = ask(c, new_thread(c))
    assert r.status_code == 200
    assert r.json()["status"] == "llm_unavailable"
    assert r.json()["answer"]["text"]  # the seller is told, in words, that it failed


# ---- rate limit ---------------------------------------------------------------------------------
def test_questions_are_rate_limited_per_tenant(store: MemoryAssistantStore) -> None:
    settings = Settings(assistant_questions_per_minute=2, _env_file=None)
    with make_client(store, settings=settings) as c:
        thread = new_thread(c)
        codes = [ask(c, thread).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_the_limiter_forgets_old_hits_and_isolates_keys() -> None:
    now = [0.0]
    limiter = RateLimiter(2, 60.0, lambda: now[0])
    a, b = uuid4(), uuid4()
    assert [limiter.allow(a), limiter.allow(a), limiter.allow(a)] == [True, True, False]
    assert limiter.allow(b)  # another tenant is unaffected
    now[0] = 61.0
    assert limiter.allow(a)
