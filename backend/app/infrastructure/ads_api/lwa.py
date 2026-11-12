"""Login with Amazon (LWA) refresh-token exchange.

Follows the general LWA documentation: a form-encoded POST with ``grant_type=refresh_token``, the
refresh token and the client credentials; the JSON response carries ``access_token`` and
``expires_in`` (and a new ``refresh_token`` only if the provider rotates it). The token URL is
CONFIGURATION, not code: regional endpoints exist and the Ads-specific details were not verified.
Error messages never include the response body, the client secret or any token.
"""

import httpx

from app.application.ads_api.errors import (
    AuthenticationError,
    NotConfiguredError,
    PermanentError,
    RateLimitedError,
    TransientError,
)
from app.application.ads_api.retry import parse_retry_after
from app.application.ads_api.token_manager import TokenResponse


class LwaTokenRefresher:
    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        if not (token_url and client_id and client_secret):
            raise NotConfiguredError("LWA token URL, client id and client secret are required")
        if not token_url.startswith("https://"):
            raise NotConfiguredError("the LWA token URL must be https")
        self._url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False)

    def __repr__(self) -> str:  # never show the secret
        return f"LwaTokenRefresher(url={self._url!r})"

    async def aclose(self) -> None:
        await self._http.aclose()

    async def refresh(self, refresh_token: str) -> TokenResponse:
        try:
            response = await self._http.post(
                self._url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        except httpx.HTTPError:  # timeouts, connection errors; the exception text is not logged
            raise TransientError("token endpoint unreachable") from None
        status = response.status_code
        if status == 429:
            raise RateLimitedError(
                "token endpoint rate limited",
                parse_retry_after(response.headers.get("Retry-After")),
            )
        if status >= 500:
            raise TransientError(f"token endpoint error ({status})")
        if status in (400, 401, 403):
            raise AuthenticationError(f"token refresh rejected ({status})")
        if status != 200:
            raise PermanentError(f"unexpected token endpoint status ({status})", status)
        try:
            body = response.json()
            token = body["access_token"]
            expires = float(body["expires_in"])
        except (ValueError, KeyError, TypeError):
            raise AuthenticationError("token endpoint returned an unusable response") from None
        rotated = body.get("refresh_token")
        return TokenResponse(
            access_token=str(token),
            expires_in=expires,
            refresh_token=str(rotated) if isinstance(rotated, str) and rotated else None,
        )
