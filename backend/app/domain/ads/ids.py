"""Deterministic internal ids: the same (tenant, kind, external id) always maps to the same UUID.

Makes syncs idempotent without lookups and keeps ids tenant-unique.
"""

from uuid import UUID, uuid5


def entity_uuid(organization_id: UUID, kind: str, external_id: str) -> UUID:
    return uuid5(organization_id, f"{kind}:{external_id}")
