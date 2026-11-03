"""Scrub secrets and personal identifiers from text before it is logged or stored in an error."""

import re

REDACTED = "[REDACTED]"
_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),  # OpenAI-style secret keys (incl. sk-proj-...)
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),  # Authorization header values
    re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password)\s*[=:]\s*\S+"),  # key=value pairs
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),  # email addresses
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key ids
]


def redact(text: str) -> str:
    """Replace anything that looks like a credential or an email address. Idempotent."""
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text
