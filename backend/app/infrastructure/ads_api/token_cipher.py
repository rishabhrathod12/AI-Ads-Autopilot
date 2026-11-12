"""Application-level encryption of refresh tokens at rest (AES-256-GCM, versioned keys).

Format: ``v1.<key id>.<base64url(nonce || ciphertext+tag)>``. The *associated data* binds a
ciphertext to its owner (organization and purpose), so a token copied to another row will not
decrypt. Keys live outside the database (``TOKEN_ENCRYPTION_KEYS``); rotating means adding a key id
and making it active: old ciphertexts keep decrypting with their own key id.
Plaintext and keys never appear in ``repr`` or in exception messages.
"""

import base64
import binascii
import json
import os
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12


class CipherConfigError(ValueError):
    """The key configuration is unusable."""


class DecryptionError(Exception):
    """The ciphertext is malformed, tampered with, bound to another owner, or its key is unknown."""


def parse_keys(raw: str) -> dict[str, bytes]:
    """Parse ``{"1": "<base64 of 32 random bytes>"}``."""
    try:
        data = json.loads(raw)
        keys = {str(k): base64.b64decode(v, validate=True) for k, v in data.items()}
    except (ValueError, TypeError, AttributeError, binascii.Error):
        raise CipherConfigError(
            "TOKEN_ENCRYPTION_KEYS must be a JSON object of base64 keys"
        ) from None
    if not keys or any(len(k) != KEY_BYTES for k in keys.values()):
        raise CipherConfigError("every encryption key must be exactly 32 bytes (base64)")
    if any("." in k or not k for k in keys):
        raise CipherConfigError("key ids must be non-empty and contain no '.'")
    return keys


class TokenCipher:
    def __init__(self, keys: Mapping[str, bytes], active_kid: str) -> None:
        if active_kid not in keys:
            raise CipherConfigError("the active key id is not among the configured keys")
        if any(len(k) != KEY_BYTES for k in keys.values()):
            raise CipherConfigError("every encryption key must be exactly 32 bytes")
        self._keys = {kid: AESGCM(k) for kid, k in keys.items()}
        self._active = active_kid

    def __repr__(self) -> str:
        return f"TokenCipher(active_kid={self._active!r}, keys={len(self._keys)})"

    def encrypt(self, plaintext: str, *, aad: str) -> str:
        nonce = os.urandom(NONCE_BYTES)
        blob = nonce + self._keys[self._active].encrypt(nonce, plaintext.encode(), aad.encode())
        return f"v1.{self._active}.{base64.urlsafe_b64encode(blob).decode()}"

    def decrypt(self, token: str, *, aad: str) -> str:
        try:
            version, kid, payload = token.split(".", 2)
            if version != "v1":
                raise DecryptionError
            blob = base64.urlsafe_b64decode(payload.encode())
            nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
            return self._keys[kid].decrypt(nonce, ciphertext, aad.encode()).decode()
        except (ValueError, KeyError, InvalidTag, binascii.Error):
            raise DecryptionError("cannot decrypt the stored credential") from None
