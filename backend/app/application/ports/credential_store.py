"""Storage port for encrypted Amazon connection credentials.

The store only ever sees ciphertext. A SQL implementation (and its migration) is NOT built: it would
add database code that cannot be verified while no test database is available, and nothing needs it
until the endpoint mapping exists. The in-memory implementation in the tests defines the contract.
"""

from typing import Protocol
from uuid import UUID


class CredentialStore(Protocol):
    async def get(self, organization_id: UUID, connection: str) -> str | None:
        """The stored ciphertext for this organization's connection, or None."""
        ...

    async def put(self, organization_id: UUID, connection: str, ciphertext: str) -> None:
        """Insert or replace the ciphertext. Never receives plaintext."""
        ...
