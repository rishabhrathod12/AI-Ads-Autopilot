"""OpenAI Chat Completions adapter over httpx (no vendor SDK; the mapping is explicit and tested).

Verified so far only against a mocked transport that follows the documented request/response
shapes. It has NOT been called against the live API; ``python -m app.cli llm-check`` does that
with a single tiny request once a key is configured.

Error messages carry the HTTP status and the provider's error *code* only, never its free-text
message (which can echo prompt content or part of a key) and never the key itself.
"""

import json
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import SecretStr

from app.application.ports.llm import (
    FinishReason,
    LLMAuthError,
    LLMBadRequestError,
    LLMRateLimitedError,
    LLMResponse,
    LLMResponseError,
    LLMUnavailableError,
    Message,
    ResponseSchema,
    ToolCall,
    ToolSpec,
    Usage,
)

_FINISH: dict[str, FinishReason] = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
    "content_filter": "content_filter",
}


def _message_to_wire(m: Message) -> dict[str, Any]:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""}
    wire: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        wire["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in m.tool_calls
        ]
    return wire


def build_request(
    model: str,
    messages: Sequence[Message],
    tools: Sequence[ToolSpec],
    response_schema: ResponseSchema | None,
    max_output_tokens: int,
    temperature: float | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [_message_to_wire(m) for m in messages],
        "max_completion_tokens": max_output_tokens,
    }
    if temperature is not None:  # some models reject the parameter, so it is optional
        body["temperature"] = temperature
    if tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]
    if response_schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": response_schema.name,
                "schema": response_schema.schema,
                "strict": True,
            },
        }
    return body


def _error_code(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error") or {}
        return str(err.get("code") or err.get("type") or "unknown")
    except (ValueError, AttributeError):
        return "unknown"


def _raise_for_status(resp: httpx.Response) -> None:
    status = resp.status_code
    if status < 400:
        return
    if status in (401, 403):
        raise LLMAuthError(f"provider rejected the credentials (HTTP {status})")
    if status == 429:
        try:
            retry_after = float(resp.headers["retry-after"])
        except (KeyError, ValueError):
            retry_after = None
        raise LLMRateLimitedError(
            f"provider rate limit (HTTP 429, {_error_code(resp)})", retry_after
        )
    if status >= 500:
        raise LLMUnavailableError(f"provider error (HTTP {status})")
    raise LLMBadRequestError(f"provider rejected the request (HTTP {status}, {_error_code(resp)})")


def parse_response(data: Any) -> LLMResponse:
    try:
        choice = data["choices"][0]
        raw = choice["message"]
        finish = _FINISH[choice["finish_reason"]]
        usage = Usage(int(data["usage"]["prompt_tokens"]), int(data["usage"]["completion_tokens"]))
        calls = []
        for tc in raw.get("tool_calls") or []:
            args = json.loads(tc["function"]["arguments"])
            if not isinstance(args, dict):
                raise TypeError("tool arguments are not an object")
            calls.append(ToolCall(str(tc["id"]), str(tc["function"]["name"]), args))
        content = raw.get("content")
        if raw.get("refusal"):
            finish, content = "content_filter", None
        return LLMResponse(
            message=Message("assistant", content, tuple(calls)),
            usage=usage,
            model=str(data.get("model", "")),
            finish_reason=finish,
            raw_id=data.get("id"),
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMResponseError(f"unusable provider response ({type(exc).__name__})") from None


class OpenAIClient:
    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._key = api_key
        self._model = model
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._http = http or httpx.AsyncClient()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        response_schema: ResponseSchema | None = None,
        max_output_tokens: int = 1024,
        temperature: float | None = 0.0,
    ) -> LLMResponse:
        body = build_request(
            self._model, messages, tools, response_schema, max_output_tokens, temperature
        )
        try:
            resp = await self._http.post(
                self._url,
                json=body,
                headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
            )
        except httpx.TimeoutException:
            raise LLMUnavailableError("provider request timed out") from None
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"network error ({type(exc).__name__})") from None
        _raise_for_status(resp)
        try:
            data = resp.json()
        except ValueError:
            raise LLMResponseError("provider returned a non-JSON body") from None
        return parse_response(data)
