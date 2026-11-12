"""Access-token lifecycle: cache, refresh ahead of expiry, one refresh at a time.

Access tokens are short-lived and kept in memory only (never stored or logged). The refresh token
comes from an encrypted credential store via ``refresh_token_source``. If the provider rotates
the refresh token, ``on_rotated`` receives the new one to re-encrypt and persist.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.application.ads_api.errors import AuthenticationError, NotConfiguredError
from app.application.ads_api.retry import RetryPolicy, with_retries


@dataclass(frozen=True)
class TokenResponse:
    access_token: str = field(repr=False)
    expires_in: float  # seconds, as the token endpoint reports it
    refresh_token: str | None = field(default=None, repr=False)  # present only if rotated


class TokenRefresher(Protocol):
    async def refresh(self, refresh_token: str) -> TokenResponse:
        """Exchange a refresh token for an access token. Raises AdsApiError subclasses."""
        ...


class TokenManager:
    def __init__(
        self,
        refresher: TokenRefresher,
        refresh_token_source: Callable[[], Awaitable[str | None]],
        *,
        on_rotated: Callable[[str], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        skew_seconds: float = 60.0,
        retry: RetryPolicy | None = None,
    ) -> None:
        self._refresher = refresher
        self._source = refresh_token_source
        self._on_rotated = on_rotated
        self._clock = clock
        self._skew = skew_seconds
        self._retry = retry or RetryPolicy(max_attempts=3)
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def _fresh(self) -> bool:
        return self._token is not None and self._clock() < self._expires_at - self._skew

    async def access_token(self) -> str:
        if self._fresh():
            return self._token  # type: ignore[return-value]
        async with self._lock:
            if self._fresh():  # another caller refreshed while we waited
                return self._token  # type: ignore[return-value]
            refresh_token = await self._source()
            if not refresh_token:
                raise NotConfiguredError("no refresh token is stored for this connection")
            response = await with_retries(
                lambda: self._refresher.refresh(refresh_token), self._retry
            )
            if not response.access_token or response.expires_in <= 0:
                raise AuthenticationError("token endpoint returned no usable access token")
            self._token = response.access_token
            self._expires_at = self._clock() + response.expires_in
            rotated = response.refresh_token
            if rotated and rotated != refresh_token and self._on_rotated is not None:
                await self._on_rotated(rotated)
            return self._token

    def invalidate(self, token: str) -> None:
        """Forget ``token`` if it is still the cached one (a 401 said it is no good)."""
        if self._token == token:
            self._token = None
            self._expires_at = 0.0
