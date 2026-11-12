"""HTTP client for the Amazon Ads API: auth, rate limiting, retries, error mapping. No endpoints.

Nothing Ads-specific is hardcoded because the documentation could not be verified: the base URL is
configuration, and the headers (``Authorization``, the client-id header, the profile-scope
header...) come from an injected ``headers_factory`` that must be written from the official
docs. Without one the client refuses to send anything.

Safety properties, all tested:
  * only paths relative to the configured base URL are accepted (no host override, no ``..``);
  * a request that declares ``mutating=True`` is refused unless the client was built with
    ``mutations_allowed=True``, which nothing in this codebase does (execution stays disabled);
  * one 401 triggers one token refresh and one retry, then AuthenticationError;
  * 429 / 5xx / timeouts are retried with backoff, honoring Retry-After when sent;
  * headers, tokens, query strings and bodies are never logged or put in exception messages.
"""

import re
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx
import structlog

from app.application.ads_api.errors import (
    AuthenticationError,
    NotConfiguredError,
    PermanentError,
    RateLimitedError,
    TransientError,
)
from app.application.ads_api.retry import (
    AsyncRateLimiter,
    RetryPolicy,
    Sleep,
    parse_retry_after,
    with_retries,
)
from app.application.ads_api.token_manager import TokenManager
from app.application.ports.ads_provider import ExecutionNotPermittedError

log = structlog.get_logger()

HeadersFactory = Callable[[str, str | None], Mapping[str, str]]  # (access_token, profile_id)
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


_PATH = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*")


def _check_path(path: str) -> None:
    """Allowlist: an absolute path of URL-safe characters. No scheme or host (so no ``//host``),
    no query or fragment (parameters go in ``params``), no backslashes, controls or dot segments."""
    if (
        not _PATH.fullmatch(path)
        or path.startswith("//")
        or any(seg in ("..", ".") for seg in path.split("/"))
    ):
        raise ValueError("path must be a plain absolute path relative to the base URL")


class AdsHttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        tokens: TokenManager,
        headers_factory: HeadersFactory | None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
        retry: RetryPolicy | None = None,
        limiter: AsyncRateLimiter | None = None,
        mutations_allowed: bool = False,
        sleep: Sleep | None = None,
    ) -> None:
        if not base_url.startswith("https://"):
            raise NotConfiguredError("the Ads API base URL must be https")
        self._tokens = tokens
        self._headers_factory = headers_factory
        self._retry = retry or RetryPolicy()
        self._limiter = limiter
        self._mutations_allowed = mutations_allowed
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            transport=transport,
            timeout=timeout,
            follow_redirects=False,  # a redirect could carry credentials to another host
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        profile_id: str | None = None,
        params: Mapping[str, str | int] | None = None,
        json: Any = None,
        mutating: bool = False,
    ) -> Any:
        method = method.upper()
        if method not in _METHODS:
            raise ValueError("unsupported HTTP method")
        _check_path(path)
        if mutating and not self._mutations_allowed:
            raise ExecutionNotPermittedError(
                "this client is read-only; changing live advertising is not permitted"
            )
        if self._headers_factory is None:
            raise NotConfiguredError(
                "no headers factory: the Ads API header scheme has not been implemented"
            )

        async def attempt() -> Any:
            if self._limiter is not None:
                await self._limiter.acquire()
            return await self._send_once(method, path, profile_id, params, json)

        kwargs: dict[str, Any] = {} if self._sleep is None else {"sleep": self._sleep}
        return await with_retries(attempt, self._retry, **kwargs)

    async def _send_once(
        self,
        method: str,
        path: str,
        profile_id: str | None,
        params: Mapping[str, str | int] | None,
        json: Any,
    ) -> Any:
        assert self._headers_factory is not None  # noqa: S101 - checked by the caller
        for auth_attempt in (1, 2):
            token = await self._tokens.access_token()
            started = time.perf_counter()
            try:
                response = await self._http.request(
                    method,
                    path,
                    params=dict(params) if params else None,
                    json=json,
                    headers=dict(self._headers_factory(token, profile_id)),
                )
            except httpx.HTTPError:
                raise TransientError("request to the Ads API failed (network or timeout)") from None
            log.info(
                "ads_api_request",
                method=method,
                path=path,  # no query string, no headers, no body
                status=response.status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            if response.status_code == 401:
                self._tokens.invalidate(token)
                if auth_attempt == 1:
                    continue  # one refresh, one retry
                raise AuthenticationError("the Ads API rejected the credentials")
            return self._interpret(response)
        raise AuthenticationError("the Ads API rejected the credentials")  # pragma: no cover

    @staticmethod
    def _interpret(response: httpx.Response) -> Any:
        status = response.status_code
        if status == 429:
            raise RateLimitedError(
                "the Ads API rate limited the request",
                parse_retry_after(response.headers.get("Retry-After")),
            )
        if status >= 500:
            raise TransientError(f"the Ads API had a server error ({status})")
        if status == 403:
            raise AuthenticationError("the Ads API denied access (403)")
        if status >= 300:  # redirects are never followed (credentials must not travel on)
            raise PermanentError(f"the Ads API rejected the request ({status})", status)
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise PermanentError("the Ads API response is too large", status)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise PermanentError("the Ads API returned a non-JSON response", status) from None
