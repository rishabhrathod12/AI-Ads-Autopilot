"""Retry/backoff, rate limiting, token manager and the refresh-token cipher (no network)."""

import asyncio
import base64
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

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
    backoff_delay,
    parse_retry_after,
    with_retries,
)
from app.application.ads_api.token_manager import TokenManager, TokenResponse
from app.infrastructure.ads_api.token_cipher import (
    CipherConfigError,
    DecryptionError,
    TokenCipher,
    parse_keys,
)
from app.infrastructure.ads_api.vault import RefreshTokenVault

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


# ---- Retry-After parsing -------------------------------------------------------------------
def test_retry_after_seconds_http_date_and_garbage() -> None:
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after("  30 ") == 30.0
    future = (NOW + timedelta(seconds=90)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(future, now=NOW) == pytest.approx(90, abs=1)
    past = (NOW - timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(past, now=NOW) == 0.0
    for bad in (None, "", "   ", "soon", "-5", "1.5", "Mon, 99 Foo 2026"):
        assert parse_retry_after(bad, now=NOW) is None


# ---- backoff and retries -------------------------------------------------------------------
def test_backoff_grows_is_capped_and_jittered_within_bounds() -> None:
    p = RetryPolicy(base_delay=1, max_delay=10, jitter=0.25)
    mid = lambda: 0.5  # noqa: E731 - jitter term is zero at 0.5
    assert [backoff_delay(i, p, mid) for i in (1, 2, 3, 4, 5)] == [1, 2, 4, 8, 10]
    assert backoff_delay(1, p, lambda: 0.0) == pytest.approx(0.75)
    assert backoff_delay(1, p, lambda: 1.0) == pytest.approx(1.25)
    assert backoff_delay(20, p, lambda: 1.0) <= 10  # never above the cap


class Flaky:
    def __init__(self, *errors: Exception, result: str = "ok") -> None:
        self.errors = list(errors)
        self.calls = 0
        self.result = result

    async def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.result


async def test_retries_transient_and_rate_limited_then_succeeds() -> None:
    sleeps: list[float] = []

    async def sleep(s: float) -> None:
        sleeps.append(s)

    op = Flaky(TransientError("a"), RateLimitedError("b"), TransientError("c"))
    assert await with_retries(op, RetryPolicy(max_attempts=5), sleep=sleep, rng=lambda: 0.5) == "ok"
    assert op.calls == 4 and sleeps == [1.0, 2.0, 4.0]


async def test_retry_after_is_honored_when_longer_than_backoff() -> None:
    sleeps: list[float] = []

    async def sleep(s: float) -> None:
        sleeps.append(s)

    op = Flaky(RateLimitedError("x", retry_after=17))
    await with_retries(op, RetryPolicy(), sleep=sleep, rng=lambda: 0.5)
    assert sleeps == [17.0]


async def test_a_retry_after_longer_than_the_cap_is_surfaced_not_slept() -> None:
    slept = False

    async def sleep(_: float) -> None:
        nonlocal slept
        slept = True

    op = Flaky(RateLimitedError("x", retry_after=3600))
    with pytest.raises(RateLimitedError) as exc:
        await with_retries(op, RetryPolicy(max_delay=60), sleep=sleep)
    assert exc.value.retry_after == 3600 and not slept and op.calls == 1


async def test_gives_up_after_max_attempts_and_never_retries_other_errors() -> None:
    async def sleep(_: float) -> None:
        return None

    always = Flaky(*[TransientError("x")] * 10)
    with pytest.raises(TransientError):
        await with_retries(always, RetryPolicy(max_attempts=3), sleep=sleep)
    assert always.calls == 3
    for err in (PermanentError("bad", 400), AuthenticationError("no"), NotConfiguredError("x")):
        op = Flaky(err)
        with pytest.raises(type(err)):
            await with_retries(op, RetryPolicy(), sleep=sleep)
        assert op.calls == 1


def test_invalid_policies_are_rejected() -> None:
    for kw in ({"max_attempts": 0}, {"base_delay": -1}, {"jitter": 1.0}, {"jitter": -0.1}):
        with pytest.raises(ValueError, match=r"invalid|jitter"):
            RetryPolicy(**kw)


# ---- rate limiter --------------------------------------------------------------------------
async def test_rate_limiter_spaces_requests_without_real_sleeping() -> None:
    clock = {"t": 0.0}
    waits: list[float] = []

    async def sleep(s: float) -> None:
        waits.append(s)
        clock["t"] += s

    limiter = AsyncRateLimiter(2.0, burst=1, clock=lambda: clock["t"], sleep=sleep)
    for _ in range(4):
        await limiter.acquire()
    assert waits == pytest.approx([0.5, 0.5, 0.5])  # 2 requests per second after the first
    with pytest.raises(ValueError, match="rate"):
        AsyncRateLimiter(0)


# ---- token manager -------------------------------------------------------------------------
class FakeRefresher:
    def __init__(self, *responses: TokenResponse | Exception) -> None:
        self.responses = list(responses)
        self.seen: list[str] = []

    async def refresh(self, refresh_token: str) -> TokenResponse:
        self.seen.append(refresh_token)
        await asyncio.sleep(0)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def manager(
    refresher: FakeRefresher, clock: dict[str, float], rotated: list[str] | None = None
) -> TokenManager:
    async def source() -> str | None:
        return "rt-1"

    async def on_rotated(new: str) -> None:
        assert rotated is not None
        rotated.append(new)

    return TokenManager(
        refresher,
        source,
        on_rotated=on_rotated if rotated is not None else None,
        clock=lambda: clock["t"],
        retry=RetryPolicy(max_attempts=1),
    )


async def test_token_is_cached_and_refreshed_before_it_expires() -> None:
    clock = {"t": 0.0}
    r = FakeRefresher(TokenResponse("A", 3600), TokenResponse("B", 3600))
    m = manager(r, clock)
    assert await m.access_token() == "A" and await m.access_token() == "A"
    assert len(r.seen) == 1
    clock["t"] = 3600 - 61  # still outside the 60 s safety margin
    assert await m.access_token() == "A"
    clock["t"] = 3600 - 59  # inside the margin: refresh early, not after it fails
    assert await m.access_token() == "B" and len(r.seen) == 2


async def test_concurrent_callers_share_one_refresh() -> None:
    r = FakeRefresher(TokenResponse("A", 3600))
    m = manager(r, {"t": 0.0})
    tokens = await asyncio.gather(*(m.access_token() for _ in range(20)))
    assert set(tokens) == {"A"} and len(r.seen) == 1


async def test_failures_are_not_cached_and_unusable_responses_are_refused() -> None:
    r = FakeRefresher(TransientError("down"), TokenResponse("A", 3600))
    m = manager(r, {"t": 0.0})
    with pytest.raises(TransientError):
        await m.access_token()
    assert await m.access_token() == "A"  # the next call tries again
    for bad in (TokenResponse("", 3600), TokenResponse("X", 0)):
        with pytest.raises(AuthenticationError):
            await manager(FakeRefresher(bad), {"t": 0.0}).access_token()


async def test_a_rotated_refresh_token_is_handed_back_for_storage() -> None:
    rotated: list[str] = []
    r = FakeRefresher(TokenResponse("A", 3600, refresh_token="rt-2"))  # noqa: S106 - fake token
    await manager(r, {"t": 0.0}, rotated).access_token()
    assert rotated == ["rt-2"]
    same: list[str] = []
    await manager(
        FakeRefresher(TokenResponse("A", 3600, refresh_token="rt-1")),  # noqa: S106 - fake token
        {"t": 0.0},
        same,
    ).access_token()
    assert same == []  # unchanged token: nothing to persist


async def test_invalidate_only_drops_the_token_it_was_told_about() -> None:
    r = FakeRefresher(TokenResponse("A", 3600), TokenResponse("B", 3600))
    m = manager(r, {"t": 0.0})
    await m.access_token()
    m.invalidate("some-other-token")
    assert await m.access_token() == "A"
    m.invalidate("A")
    assert await m.access_token() == "B"


async def test_no_stored_refresh_token_means_not_configured() -> None:
    async def source() -> str | None:
        return None

    m = TokenManager(FakeRefresher(), source)
    with pytest.raises(NotConfiguredError):
        await m.access_token()


def test_token_response_hides_secrets_in_repr() -> None:
    text = repr(TokenResponse("SECRET-ACCESS", 10, refresh_token="SECRET-REFRESH"))  # noqa: S106 - fake token
    assert "SECRET" not in text


# ---- cipher --------------------------------------------------------------------------------
def key() -> bytes:
    return os.urandom(32)


def test_encrypt_decrypt_round_trip_with_random_nonce() -> None:
    c = TokenCipher({"1": key()}, "1")
    a, b = c.encrypt("refresh-token", aad="org-1:ads"), c.encrypt("refresh-token", aad="org-1:ads")
    assert a != b and a.startswith("v1.1.")
    assert "refresh-token" not in a
    assert c.decrypt(a, aad="org-1:ads") == "refresh-token"


def test_ciphertext_is_bound_to_its_owner_and_tamper_evident() -> None:
    c = TokenCipher({"1": key()}, "1")
    blob = c.encrypt("rt", aad="org-1:ads")
    with pytest.raises(DecryptionError):
        c.decrypt(blob, aad="org-2:ads")  # copied to another organization
    head, kid, payload = blob.split(".")
    raw = bytearray(base64.urlsafe_b64decode(payload))
    raw[-1] ^= 1
    with pytest.raises(DecryptionError):
        c.decrypt(f"{head}.{kid}.{base64.urlsafe_b64encode(bytes(raw)).decode()}", aad="org-1:ads")
    for junk in ("", "x", "v2.1.AAAA", "v1.9.AAAA", "v1.1.!!!", "v1.1."):
        with pytest.raises(DecryptionError):
            c.decrypt(junk, aad="org-1:ads")


def test_key_rotation_keeps_old_ciphertexts_readable() -> None:
    k1, k2 = key(), key()
    old = TokenCipher({"1": k1}, "1").encrypt("rt", aad="a")
    rotated = TokenCipher({"1": k1, "2": k2}, "2")
    assert rotated.decrypt(old, aad="a") == "rt"
    assert rotated.encrypt("rt", aad="a").startswith("v1.2.")
    with pytest.raises(DecryptionError):  # the old key is gone: cannot read
        TokenCipher({"2": k2}, "2").decrypt(old, aad="a")
    with pytest.raises(DecryptionError):  # wrong key material
        TokenCipher({"1": key()}, "1").decrypt(old, aad="a")


def test_cipher_never_reveals_keys_or_plaintext_in_text() -> None:
    c = TokenCipher({"1": key()}, "1")
    assert "1" in repr(c) and "bytes" not in repr(c)
    with pytest.raises(DecryptionError) as exc:
        c.decrypt(c.encrypt("topsecret", aad="a"), aad="b")
    assert "topsecret" not in str(exc.value)


def test_key_configuration_is_validated() -> None:
    good = base64.b64encode(key()).decode()
    assert set(parse_keys(f'{{"1": "{good}", "2": "{good}"}}')) == {"1", "2"}
    short = base64.b64encode(b"short").decode()
    for raw in (
        "not json",
        "[]",
        '{"1": 5}',
        "{}",
        f'{{"1": "{short}"}}',
        f'{{"a.b": "{good}"}}',
        f'{{"": "{good}"}}',
    ):
        with pytest.raises(CipherConfigError):
            parse_keys(raw)
    with pytest.raises(CipherConfigError):
        TokenCipher({"1": key()}, "missing")


# ---- vault ---------------------------------------------------------------------------------
class MemoryCredentialStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str], str] = {}

    async def get(self, organization_id: UUID, connection: str) -> str | None:
        return self.rows.get((organization_id, connection))

    async def put(self, organization_id: UUID, connection: str, ciphertext: str) -> None:
        self.rows[(organization_id, connection)] = ciphertext


ORG_A, ORG_B = UUID(int=1), UUID(int=2)


async def test_vault_stores_only_ciphertext_and_round_trips() -> None:
    store = MemoryCredentialStore()
    vault = RefreshTokenVault(store, TokenCipher({"1": key()}, "1"))
    await vault.save(ORG_A, "ads", "Atzr|very-secret-refresh-token")
    assert "very-secret" not in repr(store.rows)
    assert await vault.load(ORG_A, "ads") == "Atzr|very-secret-refresh-token"
    assert await vault.load(ORG_A, "other") is None and await vault.load(ORG_B, "ads") is None


async def test_a_token_copied_to_another_organization_or_connection_is_unusable() -> None:
    store = MemoryCredentialStore()
    vault = RefreshTokenVault(store, TokenCipher({"1": key()}, "1"))
    await vault.save(ORG_A, "ads", "rt-secret")
    store.rows[(ORG_B, "ads")] = store.rows[(ORG_A, "ads")]  # a row copied across tenants
    store.rows[(ORG_A, "ads-2")] = store.rows[(ORG_A, "ads")]  # or across connections
    assert await vault.load(ORG_B, "ads") is None
    assert await vault.load(ORG_A, "ads-2") is None
    assert await vault.load(ORG_A, "ads") == "rt-secret"


async def test_vault_treats_tampered_or_unreadable_ciphertext_as_no_token() -> None:
    store = MemoryCredentialStore()
    vault = RefreshTokenVault(store, TokenCipher({"1": key()}, "1"))
    await vault.save(ORG_A, "ads", "rt")
    store.rows[(ORG_A, "ads")] = store.rows[(ORG_A, "ads")][:-4] + "AAAA"
    assert await vault.load(ORG_A, "ads") is None
    with pytest.raises(ValueError, match="empty"):
        await vault.save(ORG_A, "ads", "")


async def test_vault_feeds_the_token_manager_including_rotation() -> None:
    store = MemoryCredentialStore()
    vault = RefreshTokenVault(store, TokenCipher({"1": key()}, "1"))
    await vault.save(ORG_A, "ads", "rt-1")

    async def source() -> str | None:
        return await vault.load(ORG_A, "ads")

    async def rotated(new: str) -> None:
        await vault.save(ORG_A, "ads", new)

    refresher = FakeRefresher(TokenResponse("A", 3600, refresh_token="rt-2"))  # noqa: S106
    await TokenManager(refresher, source, on_rotated=rotated).access_token()
    assert refresher.seen == ["rt-1"]
    assert await vault.load(ORG_A, "ads") == "rt-2"  # the rotated token replaced the old one
