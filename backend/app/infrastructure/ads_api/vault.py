"""Refresh-token vault: encrypts before storing, decrypts on demand, binds tokens to their owner.

The associated data is ``<organization id>:<connection>``, so a ciphertext copied to another
organization's row (or another connection) fails to decrypt. A token that cannot be decrypted is
reported as *no token* (``None``) rather than raising a message that might leak details.
"""

from uuid import UUID

from app.application.ports.credential_store import CredentialStore
from app.infrastructure.ads_api.token_cipher import DecryptionError, TokenCipher


class RefreshTokenVault:
    def __init__(self, store: CredentialStore, cipher: TokenCipher) -> None:
        self._store = store
        self._cipher = cipher

    @staticmethod
    def _aad(organization_id: UUID, connection: str) -> str:
        return f"{organization_id}:{connection}"

    async def save(self, organization_id: UUID, connection: str, refresh_token: str) -> None:
        if not refresh_token:
            raise ValueError("refresh token must not be empty")
        blob = self._cipher.encrypt(refresh_token, aad=self._aad(organization_id, connection))
        await self._store.put(organization_id, connection, blob)

    async def load(self, organization_id: UUID, connection: str) -> str | None:
        blob = await self._store.get(organization_id, connection)
        if blob is None:
            return None
        try:
            return self._cipher.decrypt(blob, aad=self._aad(organization_id, connection))
        except DecryptionError:
            return None  # tampered, moved, or encrypted with a key that is no longer configured
