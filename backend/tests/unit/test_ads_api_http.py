"""HTTP client, LWA refresher, provider skeleton and Amazon config validation (mock transports)."""

import json
from collections.abc import Callable
from datetime import date
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from app.api.main import create_app
from app.application.ads_api.errors import (
    AuthenticationError,
    NotConfiguredError,
    PermanentError,
    RateLimitedError,
    TransientError,
)
from app.application.ads_api.retry import AsyncRateLimiter, RetryPolicy
from app.application.ads_api.token_manager import TokenManager, TokenResponse
from app.application.ports.ads_provider import (
    ExecutionNotPermittedError,
    ProviderNotAvailableError,
)
from app.config import Settings
from app.domain.ads.models import ChangeRequest, DataSource, EntityType
from app.infrastructure.ads_api.http_client import MAX_RESPONSE_BYTES, AdsHttpClient
from app.infrastructure.ads_api.lwa import LwaTokenRefresher
from app.infrastructure.providers.amazon.provider import AmazonAdsApiProvider
from app.infrastructure.providers.factory import build_provider
from app.logging import configure_logging
from app.startup_checks import UnsafeConfigurationError, validate_amazon_ads_config

BASE = "https://ads.example.test"
TOKEN_URL = "https://auth.example.test/token"  # noqa: S105 - a URL, not a secret
SECRET_TOKEN = "access-token-SECRET-123"  # noqa: S105
SECRET_BODY = "client-secret-ZZZ"  # noqa: S105
Handler = Callable[[httpx.Request], httpx.Response]


class CountingRefresher:
    def __init__(self) -> None:
        self.calls = 0

    async def refresh(self, refresh_token: str) -> TokenResponse:
        self.calls += 1
        return TokenResponse(f"{SECRET_TOKEN}-{self.calls}", 3600)


def make_client(
    handler: Handler,
    *,
    mutations_allowed: bool = False,
    headers: bool = True,
    attempts: int = 3,
    sleeps: list[float] | None = None,
    limiter: AsyncRateLimiter | None = None,
) -> tuple[AdsHttpClient, CountingRefresher]:
    refresher = CountingRefresher()

    async def source() -> str | None:
        return "rt"

    async def sleep(s: float) -> None:
        if sleeps is not None:
            sleeps.append(s)

    tokens = TokenManager(refresher, source, retry=RetryPolicy(max_attempts=1))
    client = AdsHttpClient(
        base_url=BASE,
        tokens=tokens,
        headers_factory=(lambda tok, pid: {"X-Test-Auth": tok, "X-Test-Profile": pid or ""})
        if headers
        else None,
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=attempts, base_delay=1, max_delay=60),
        limiter=limiter,
        mutations_allowed=mutations_allowed,
        sleep=sleep,
    )
    return client, refresher


def ok(body: Any = None) -> httpx.Response:
    return httpx.Response(200, json={"ok": True} if body is None else body)


# ---- request construction and safety -------------------------------------------------------
async def test_a_request_uses_the_base_url_headers_and_query_and_returns_json() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return ok({"items": [1, 2]})

    client, _ = make_client(handler)
    out = await client.request_json("GET", "/things", profile_id="p-1", params={"limit": 5})
    assert out == {"items": [1, 2]}
    req = seen[0]
    assert str(req.url) == f"{BASE}/things?limit=5"
    assert (
        req.headers["X-Test-Auth"].startswith(SECRET_TOKEN)
        and req.headers["X-Test-Profile"] == "p-1"
    )


async def test_without_a_headers_factory_nothing_is_sent() -> None:
    sent = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent = True
        return ok()

    client, _ = make_client(handler, headers=False)
    with pytest.raises(NotConfiguredError, match="headers"):
        await client.request_json("GET", "/x")
    assert not sent


@pytest.mark.parametrize(
    "path",
    [
        "x",
        "http://evil.test/x",
        "//evil.test/x",
        "/a/../b",
        "/a/./b",
        "/a\\b",
        "/a\nb",
        "/a?x=1",
        "/a#frag",
        "/a b",
        "",
    ],
)
async def test_only_plain_relative_paths_are_accepted(path: str) -> None:
    client, _ = make_client(lambda _r: ok())
    with pytest.raises(ValueError, match="path"):
        await client.request_json("GET", path)


async def test_unknown_methods_are_refused() -> None:
    client, _ = make_client(lambda _r: ok())
    with pytest.raises(ValueError, match="method"):
        await client.request_json("TRACE", "/x")


async def test_mutations_are_refused_unless_the_client_was_explicitly_built_for_them() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return ok()

    client, _ = make_client(handler)
    with pytest.raises(ExecutionNotPermittedError):
        await client.request_json("POST", "/change", json={"bid": 1}, mutating=True)
    assert calls == 0
    # a read that happens to be a POST (e.g. requesting a report) is a different, declared case
    assert await client.request_json("POST", "/reports", json={}, mutating=False) == {"ok": True}
    writer, _ = make_client(handler, mutations_allowed=True)
    assert await writer.request_json("POST", "/change", json={}, mutating=True) == {"ok": True}


async def test_the_base_url_must_be_https() -> None:
    with pytest.raises(NotConfiguredError, match="https"):
        AdsHttpClient(base_url="http://ads.example.test", tokens=None, headers_factory=None)  # type: ignore[arg-type]


# ---- authentication ------------------------------------------------------------------------
async def test_one_401_triggers_one_refresh_and_one_retry() -> None:
    tokens_seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        tokens_seen.append(req.headers["X-Test-Auth"])
        return httpx.Response(401) if len(tokens_seen) == 1 else ok()

    client, refresher = make_client(handler)
    assert await client.request_json("GET", "/x") == {"ok": True}
    assert refresher.calls == 2 and tokens_seen[0] != tokens_seen[1]


async def test_repeated_401_gives_up_instead_of_looping() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, text=f"bad token {SECRET_TOKEN}")

    client, refresher = make_client(handler)
    with pytest.raises(AuthenticationError) as exc:
        await client.request_json("GET", "/x")
    assert calls == 2 and refresher.calls == 2
    assert SECRET_TOKEN not in str(exc.value)  # the response body is never echoed


async def test_403_is_an_authentication_error_without_retry() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403)

    client, _ = make_client(handler)
    with pytest.raises(AuthenticationError):
        await client.request_json("GET", "/x")
    assert calls == 1


# ---- rate limiting, retries and error mapping ----------------------------------------------
async def test_429_honors_retry_after_then_succeeds() -> None:
    sleeps: list[float] = []
    replies = [httpx.Response(429, headers={"Retry-After": "9"}), ok()]
    client, _ = make_client(lambda _r: replies.pop(0), sleeps=sleeps)
    assert await client.request_json("GET", "/x") == {"ok": True}
    assert sleeps and sleeps[0] >= 9


async def test_429_without_retry_after_still_backs_off() -> None:
    sleeps: list[float] = []
    replies = [httpx.Response(429), httpx.Response(429), ok()]
    client, _ = make_client(lambda _r: replies.pop(0), sleeps=sleeps)
    await client.request_json("GET", "/x")
    assert len(sleeps) == 2 and sleeps[1] > 0


async def test_a_huge_retry_after_is_reported_not_slept() -> None:
    sleeps: list[float] = []
    client, _ = make_client(
        lambda _r: httpx.Response(429, headers={"Retry-After": "86400"}), sleeps=sleeps
    )
    with pytest.raises(RateLimitedError) as exc:
        await client.request_json("GET", "/x")
    assert exc.value.retry_after == 86400 and sleeps == []


async def test_5xx_and_network_errors_are_retried_then_surface_as_transient() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectTimeout("secret-host.internal timed out")

    client, _ = make_client(handler, attempts=3, sleeps=[])
    with pytest.raises(TransientError) as exc:
        await client.request_json("GET", "/x")
    assert calls == 3 and "secret-host" not in str(exc.value)
    server_errors = [httpx.Response(503), httpx.Response(500), ok()]
    client2, _ = make_client(lambda _r: server_errors.pop(0), sleeps=[])
    assert await client2.request_json("GET", "/x") == {"ok": True}


@pytest.mark.parametrize("status", [400, 404, 409, 422])
async def test_other_4xx_are_permanent_and_not_retried(status: int) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="details with secrets")

    client, _ = make_client(handler)
    with pytest.raises(PermanentError) as exc:
        await client.request_json("GET", "/x")
    assert calls == 1 and exc.value.status == status and "secrets" not in str(exc.value)


async def test_redirects_are_not_followed_and_are_errors() -> None:
    hosts: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        hosts.append(req.url.host)
        return httpx.Response(302, headers={"Location": "https://evil.test/steal"})

    client, _ = make_client(handler)
    with pytest.raises(PermanentError):
        await client.request_json("GET", "/x")
    assert hosts == ["ads.example.test"]  # never contacted the redirect target


async def test_empty_non_json_and_oversized_responses() -> None:
    client, _ = make_client(lambda _r: httpx.Response(204))
    assert await client.request_json("GET", "/x") is None
    client, _ = make_client(lambda _r: httpx.Response(200, text="<html>"))
    with pytest.raises(PermanentError, match="non-JSON"):
        await client.request_json("GET", "/x")
    client, _ = make_client(lambda _r: httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1)))
    with pytest.raises(PermanentError, match="too large"):
        await client.request_json("GET", "/x")


async def test_every_attempt_goes_through_the_rate_limiter() -> None:
    acquired = 0

    class CountingLimiter(AsyncRateLimiter):
        async def acquire(self) -> None:
            nonlocal acquired
            acquired += 1

    replies = [httpx.Response(503), ok()]
    client, _ = make_client(lambda _r: replies.pop(0), limiter=CountingLimiter(5), sleeps=[])
    await client.request_json("GET", "/x")
    assert acquired == 2


async def test_logs_contain_neither_credentials_nor_query_strings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    client, _ = make_client(lambda _r: ok())
    await client.request_json("GET", "/campaigns", profile_id="p-77", params={"q": "private-term"})
    out = capsys.readouterr().out
    entry = next(json.loads(line) for line in out.splitlines() if "ads_api_request" in line)
    assert entry["path"] == "/campaigns" and entry["status"] == 200 and "duration_ms" in entry
    for forbidden in (SECRET_TOKEN, "private-term", "X-Test-Auth", "p-77"):
        assert forbidden not in out


# ---- LWA refresher -------------------------------------------------------------------------
def lwa(handler: Handler) -> LwaTokenRefresher:
    return LwaTokenRefresher(
        token_url=TOKEN_URL,
        client_id="client-1",
        client_secret=SECRET_BODY,
        transport=httpx.MockTransport(handler),
    )


async def test_lwa_posts_a_form_with_the_refresh_grant_and_parses_the_response() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200, json={"access_token": "A1", "expires_in": 3600, "token_type": "bearer"}
        )

    out = await lwa(handler).refresh("rt-9")
    form = parse_qs(seen[0].content.decode())
    assert seen[0].method == "POST" and str(seen[0].url) == TOKEN_URL
    assert form == {
        "grant_type": ["refresh_token"],
        "refresh_token": ["rt-9"],
        "client_id": ["client-1"],
        "client_secret": [SECRET_BODY],
    }
    assert out.access_token == "A1" and out.expires_in == 3600  # noqa: S105
    assert out.refresh_token is None


async def test_lwa_returns_a_rotated_refresh_token() -> None:
    body = {"access_token": "A", "expires_in": 10, "refresh_token": "rt-NEW"}
    out = await lwa(lambda _r: httpx.Response(200, json=body)).refresh("rt-old")
    assert out.refresh_token == "rt-NEW"  # noqa: S105


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            httpx.Response(400, json={"error": "invalid_grant", "hint": SECRET_BODY}),
            AuthenticationError,
        ),
        (httpx.Response(401), AuthenticationError),
        (httpx.Response(429, headers={"Retry-After": "3"}), RateLimitedError),
        (httpx.Response(503), TransientError),
        (httpx.Response(302, headers={"Location": "https://evil.test"}), PermanentError),
        (httpx.Response(200, text="not json"), AuthenticationError),
        (httpx.Response(200, json={"access_token": "A"}), AuthenticationError),  # no expires_in
        (httpx.Response(200, json={"expires_in": 5}), AuthenticationError),  # no access_token
    ],
)
async def test_lwa_failures_map_to_typed_errors_without_leaking_bodies(
    response: httpx.Response, error: type[Exception]
) -> None:
    with pytest.raises(error) as exc:
        await lwa(lambda _r: response).refresh("rt")
    text = str(exc.value)
    assert SECRET_BODY not in text and text != "rt" and "client-secret" not in text


async def test_lwa_network_failure_is_transient_and_rate_limit_carries_retry_after() -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-host failed")

    with pytest.raises(TransientError) as exc:
        await lwa(boom).refresh("rt")
    assert "secret-host" not in str(exc.value)
    with pytest.raises(RateLimitedError) as rl:
        await lwa(lambda _r: httpx.Response(429, headers={"Retry-After": "12"})).refresh("rt")
    assert rl.value.retry_after == 12


def test_lwa_requires_full_https_configuration_and_hides_the_secret() -> None:
    bad_configs: list[dict[str, Any]] = [
        {"token_url": "", "client_id": "c", "client_secret": "s"},
        {"token_url": TOKEN_URL, "client_id": "", "client_secret": "s"},
        {"token_url": TOKEN_URL, "client_id": "c", "client_secret": ""},
        {"token_url": "http://auth.example.test/token", "client_id": "c", "client_secret": "s"},
    ]
    for kw in bad_configs:
        with pytest.raises(NotConfiguredError):
            LwaTokenRefresher(**kw)
    assert SECRET_BODY not in repr(lwa(lambda _r: ok()))


# ---- provider skeleton ---------------------------------------------------------------------
@pytest.fixture
def provider() -> AmazonAdsApiProvider:
    client, _ = make_client(lambda _r: ok())
    return AmazonAdsApiProvider(client)


async def test_the_provider_has_no_endpoint_mapping_and_never_executes(
    provider: AmazonAdsApiProvider,
) -> None:
    assert provider.source is DataSource.AMAZON_ADS_API
    for call in (
        provider.list_profiles(),
        provider.list_campaigns("p"),
        provider.list_ad_groups("p"),
        provider.list_targets("p"),
        provider.list_search_terms("p"),
    ):
        with pytest.raises(ProviderNotAvailableError, match="mapping"):
            await call
    with pytest.raises(ProviderNotAvailableError):
        async for _ in provider.fetch_daily_metrics(
            "p", EntityType.CAMPAIGN, date(2026, 1, 1), date(2026, 1, 2)
        ):
            pass
    change = ChangeRequest.model_construct(
        entity_type=EntityType.TARGET, entity_id="t", field="bid_paise", new_value=100
    )
    with pytest.raises(ExecutionNotPermittedError):
        await provider.execute_change("p", change)


def test_amazon_stays_unwired_in_the_provider_factory() -> None:
    with pytest.raises(ProviderNotAvailableError):
        build_provider(Settings(_env_file=None, ads_provider="amazon"))


# ---- configuration validation --------------------------------------------------------------
GOOD_KEYS = json.dumps({"1": "A" * 43 + "="})  # 32 bytes of base64 'A' padding => valid length


def amazon_settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "ads_provider": "amazon",
        "amazon_ads_enabled": True,
        "amazon_ads_client_id": "client-1",
        "amazon_ads_client_secret": "s3cret",
        "amazon_ads_token_url": TOKEN_URL,
        "amazon_ads_api_base_url": BASE,
        "token_encryption_keys": GOOD_KEYS,
        "token_encryption_active_kid": "1",
    }
    return Settings(_env_file=None, **(base | kw))


def test_the_defaults_enable_nothing() -> None:
    s = Settings(_env_file=None)
    assert s.ads_provider == "mock" and s.amazon_ads_enabled is False
    assert s.ad_execution_enabled is False
    validate_amazon_ads_config(s)
    create_app(s)


def test_a_complete_amazon_configuration_validates() -> None:
    validate_amazon_ads_config(amazon_settings())


@pytest.mark.parametrize(
    ("kw", "fragment"),
    [
        ({"amazon_ads_enabled": False}, "together"),
        ({"ads_provider": "mock"}, "together"),
        ({"amazon_ads_client_id": ""}, "CLIENT_ID"),
        ({"amazon_ads_client_secret": None}, "CLIENT_SECRET"),
        ({"amazon_ads_client_secret": ""}, "CLIENT_SECRET"),  # an empty value is not a secret
        ({"amazon_ads_token_url": "http://auth.example.test/token"}, "TOKEN_URL"),
        ({"amazon_ads_token_url": ""}, "TOKEN_URL"),
        ({"amazon_ads_api_base_url": "http://ads.example.test"}, "API_BASE_URL"),
        ({"amazon_ads_requests_per_second": 0}, "REQUESTS_PER_SECOND"),
        ({"token_encryption_keys": None}, "TOKEN_ENCRYPTION_KEYS"),
        ({"token_encryption_keys": ""}, "TOKEN_ENCRYPTION_KEYS"),
        ({"token_encryption_keys": "not json"}, "TOKEN_ENCRYPTION_KEYS"),
        ({"token_encryption_active_kid": "9"}, "ACTIVE_KID"),
    ],
)
def test_incomplete_or_unsafe_amazon_configuration_is_refused(
    kw: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(UnsafeConfigurationError, match=fragment):
        create_app(amazon_settings(**kw))


def test_enabling_amazon_never_enables_execution() -> None:
    # Even a complete Amazon configuration cannot turn on execution: its prerequisites do not exist.
    with pytest.raises(UnsafeConfigurationError, match="approval service"):
        create_app(amazon_settings(ad_execution_enabled=True))
