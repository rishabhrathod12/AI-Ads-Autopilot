"""JWT verification against a provider's published keys (JWKS). Provider-independent.

Only asymmetric algorithms are accepted, so an attacker cannot present an HMAC token signed with a
public key, and ``alg: none`` is never valid. Issuer, audience, expiry and subject are all required.
Failure reasons are not returned to callers and tokens are never logged.
"""

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

import jwt

from app.application.ports.auth import AuthError, Claims

ASYMMETRIC_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512", "EdDSA"}
)
MAX_TOKEN_CHARS = 8192
MAX_SUBJECT_CHARS = 255
LEEWAY_SECONDS = 30

KeyResolver = Callable[[str], Any]  # token -> verification key


def jwks_key_resolver(jwks_url: str) -> KeyResolver:
    """Resolve the signing key named by the token's ``kid`` from the provider's JWKS (cached)."""
    client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=600, timeout=5)

    def resolve(token: str) -> Any:
        return client.get_signing_key_from_jwt(token).key

    return resolve


class JwtVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        key_resolver: KeyResolver,
        algorithms: Sequence[str] = ("RS256", "ES256"),
    ) -> None:
        bad = set(algorithms) - ASYMMETRIC_ALGORITHMS
        if bad or not algorithms:
            raise ValueError(f"only asymmetric JWT algorithms are allowed, got {sorted(bad)}")
        self._issuer = issuer
        self._audience = audience
        self._resolve = key_resolver
        self._algorithms = list(algorithms)

    async def verify(self, token: str) -> Claims:
        if not token or len(token) > MAX_TOKEN_CHARS:
            raise AuthError
        try:
            key = await asyncio.to_thread(self._resolve, token)  # may fetch JWKS over the network
            payload = jwt.decode(
                token,
                key,
                algorithms=self._algorithms,
                issuer=self._issuer,
                audience=self._audience,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except (jwt.PyJWTError, ValueError, OSError, TypeError):
            raise AuthError from None
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject or len(subject) > MAX_SUBJECT_CHARS:
            raise AuthError
        return Claims(subject=subject)
