"""The OpenAI adapter against a mocked transport (documented request/response shapes).

This proves our mapping and error handling. It does NOT prove the live API accepts the request:
that is what `python -m app.cli llm-check` is for, once a key is configured.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.application.ports.llm import (
    LLMAuthError,
    LLMBadRequestError,
    LLMRateLimitedError,
    LLMResponseError,
    LLMUnavailableError,
    Message,
    ResponseSchema,
    ToolCall,
    ToolSpec,
)
from app.infrastructure.llm.openai_client import OpenAIClient, build_request

KEY = "sk-test-NEVER-LEAK-0123456789"
Handler = Callable[[httpx.Request], httpx.Response]


def client(handler: Handler, base_url: str = "https://api.example.test/v1") -> OpenAIClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAIClient(api_key=SecretStr(KEY), model="test-model", base_url=base_url, http=http)


def ok(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "chatcmpl-1",
        "model": "test-model-2026",
        "choices": [
            {"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    }
    body.update(overrides)
    return body


TOOL = ToolSpec("get_summary", "Summarise the account", {"type": "object", "properties": {}})
HELLO = [Message("user", "hi")]


# ---- request -------------------------------------------------------------------------------
async def test_request_shape() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ok())

    history = [
        Message("system", "You are careful."),
        Message("user", "Why did ACOS rise?"),
        Message("assistant", None, (ToolCall("call_1", "get_summary", {"days": 28}),)),
        Message("tool", '{"acos": 20}', tool_call_id="call_1"),
    ]
    schema = ResponseSchema("answer", {"type": "object", "properties": {"a": {"type": "string"}}})
    await client(handler).complete(
        history, tools=[TOOL], response_schema=schema, max_output_tokens=300, temperature=0.0
    )

    assert seen["url"] == "https://api.example.test/v1/chat/completions"
    assert seen["auth"] == f"Bearer {KEY}"
    body = seen["body"]
    assert body["model"] == "test-model"
    assert body["max_completion_tokens"] == 300
    assert body["temperature"] == 0.0
    assert body["messages"][0] == {"role": "system", "content": "You are careful."}
    assistant = body["messages"][2]
    assert assistant["content"] is None
    assert assistant["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_summary", "arguments": '{"days": 28}'},
        }
    ]
    assert body["messages"][3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"acos": 20}',
    }
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_summary",
                "description": "Summarise the account",
                "parameters": TOOL.parameters,
            },
        }
    ]
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "answer", "schema": schema.schema, "strict": True},
    }


def test_optional_fields_are_omitted_when_not_asked_for() -> None:
    body = build_request("m", HELLO, [], None, 50, None)
    assert "temperature" not in body  # some models reject it
    assert "tools" not in body
    assert "response_format" not in body
    assert body["max_completion_tokens"] == 50


async def test_base_url_trailing_slash_is_normalised() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json=ok())

    await client(handler, "https://proxy.example.test/openai/").complete(HELLO)
    assert urls == ["https://proxy.example.test/openai/chat/completions"]


# ---- response ------------------------------------------------------------------------------
async def test_text_response_is_parsed() -> None:
    r = await client(lambda _: httpx.Response(200, json=ok())).complete(HELLO)
    assert r.message == Message("assistant", "Hello")
    assert (r.usage.input_tokens, r.usage.output_tokens) == (12, 3)
    assert r.model == "test-model-2026"
    assert r.finish_reason == "stop"
    assert r.raw_id == "chatcmpl-1"


async def test_tool_calls_are_parsed_with_json_arguments() -> None:
    body = ok(
        choices=[
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "get_summary", "arguments": '{"days": 7}'},
                        },
                        {
                            "id": "c2",
                            "type": "function",
                            "function": {"name": "anomalies", "arguments": "{}"},
                        },
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    )
    r = await client(lambda _: httpx.Response(200, json=body)).complete(HELLO, tools=[TOOL])
    assert r.finish_reason == "tool_calls"
    assert r.message.content is None
    assert r.message.tool_calls == (
        ToolCall("c1", "get_summary", {"days": 7}),
        ToolCall("c2", "anomalies", {}),
    )


@pytest.mark.parametrize(
    ("wire", "expected"), [("length", "length"), ("content_filter", "content_filter")]
)
async def test_finish_reasons_map(wire: str, expected: str) -> None:
    body = ok(choices=[{"message": {"content": "x"}, "finish_reason": wire}])
    r = await client(lambda _: httpx.Response(200, json=body)).complete(HELLO)
    assert r.finish_reason == expected


async def test_a_refusal_is_reported_as_a_content_filter_not_as_an_answer() -> None:
    body = ok(
        choices=[
            {
                "message": {"content": None, "refusal": "I can't help with that"},
                "finish_reason": "stop",
            }
        ]
    )
    r = await client(lambda _: httpx.Response(200, json=body)).complete(HELLO)
    assert r.finish_reason == "content_filter"
    assert r.message.content is None


# ---- errors --------------------------------------------------------------------------------
LEAKY = {
    "error": {
        "message": f"Incorrect API key provided: {KEY}. See your prompt: hi",
        "code": "invalid_api_key",
    }
}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, LLMAuthError),
        (403, LLMAuthError),
        (400, LLMBadRequestError),
        (404, LLMBadRequestError),
        (422, LLMBadRequestError),
        (500, LLMUnavailableError),
        (502, LLMUnavailableError),
        (503, LLMUnavailableError),
        (429, LLMRateLimitedError),
    ],
)
async def test_http_errors_map_to_typed_errors_without_leaking(
    status: int, expected: type[Exception]
) -> None:
    with pytest.raises(expected) as exc:
        await client(lambda _: httpx.Response(status, json=LEAKY)).complete(HELLO)
    text = str(exc.value)
    assert KEY not in text
    assert "Incorrect API key" not in text  # the provider's free text can echo secrets or prompts
    assert "prompt" not in text


async def test_bad_request_reports_the_error_code_only() -> None:
    body = {"error": {"message": "secret detail", "code": "context_length_exceeded"}}
    with pytest.raises(LLMBadRequestError, match="context_length_exceeded"):
        await client(lambda _: httpx.Response(400, json=body)).complete(HELLO)


async def test_rate_limit_carries_retry_after_when_given() -> None:
    with pytest.raises(LLMRateLimitedError) as exc:
        await client(lambda _: httpx.Response(429, headers={"Retry-After": "2"}, json={})).complete(
            HELLO
        )
    assert exc.value.retry_after == 2.0
    with pytest.raises(LLMRateLimitedError) as exc2:
        await client(lambda _: httpx.Response(429, json={})).complete(HELLO)
    assert exc2.value.retry_after is None


@pytest.mark.parametrize(
    "body",
    [
        {},  # no choices
        {"choices": []},
        ok(choices=[{"message": {"content": "x"}, "finish_reason": "banana"}]),  # unknown finish
        ok(usage=None),  # cost accounting needs usage
        {k: v for k, v in ok().items() if k != "usage"},
        ok(
            choices=[
                {
                    "message": {
                        "tool_calls": [
                            {"id": "c", "function": {"name": "t", "arguments": "{not json"}}
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        ),
        ok(
            choices=[
                {
                    "message": {
                        "tool_calls": [{"id": "c", "function": {"name": "t", "arguments": "[1]"}}]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        ),
    ],
)
async def test_unusable_responses_raise_a_response_error(body: dict[str, Any]) -> None:
    with pytest.raises(LLMResponseError) as exc:
        await client(lambda _: httpx.Response(200, json=body)).complete(HELLO)
    assert KEY not in str(exc.value)


async def test_a_non_json_body_is_a_response_error() -> None:
    with pytest.raises(LLMResponseError):
        await client(lambda _: httpx.Response(200, content=b"<html>gateway</html>")).complete(HELLO)


@pytest.mark.parametrize("failure", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
async def test_network_failures_are_unavailable_and_retryable(failure: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise failure

    with pytest.raises(LLMUnavailableError) as exc:
        await client(handler).complete(HELLO)
    assert KEY not in str(exc.value)
