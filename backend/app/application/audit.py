"""Building audit entries: one place that scrubs secrets from whatever gets recorded."""

import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from app.application.llm.redact import redact_obj
from app.domain.audit import AuditEntry


def audit_entry(
    *,
    org: UUID,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    now: datetime,
    request_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditEntry:
    """Free text (comments, reasons) is scrubbed of credentials and emails before it is stored."""
    return AuditEntry(
        id=uuid.uuid4(),
        organization_id=org,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=redact_obj(before) if before is not None else None,
        after=redact_obj(after) if after is not None else None,
        request_id=request_id,
        created_at=now,
    )
