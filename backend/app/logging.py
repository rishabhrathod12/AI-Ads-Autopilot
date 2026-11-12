"""Structured JSON logging with secret redaction (by field name AND by value).

Two layers, because secrets leak both ways: a field called ``api_key`` is replaced outright, and any
string value anywhere in the event (messages, exception text, nested data) is scrubbed for key-like
tokens, bearer credentials and email addresses. Exceptions are logged as a type, a scrubbed and
truncated message, and traceback frames (file, line, function) only: never local variable values.
"""

import logging
import traceback
from typing import Any

import structlog
from structlog.types import EventDict

from app.application.llm.redact import redact_obj

_SENSITIVE = ("secret", "token", "password", "api_key", "apikey", "authorization", "credential")
REDACTED = "[REDACTED]"
MAX_MESSAGE_CHARS = 300
MAX_FRAMES = 20


def redact_secrets(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    for key in list(event_dict):
        if any(s in key.lower() for s in _SENSITIVE):
            event_dict[key] = REDACTED
        else:
            event_dict[key] = redact_obj(event_dict[key])
    return event_dict


def exception_details(exc: BaseException) -> dict[str, Any]:
    """What is safe and useful to log about an exception. Never includes variable values."""
    frames = traceback.extract_tb(exc.__traceback__)[-MAX_FRAMES:]
    return {
        "error_type": type(exc).__name__,
        "error_message": redact_obj(str(exc))[:MAX_MESSAGE_CHARS],
        "frames": [
            f"{f.filename.replace(chr(92), '/').rsplit('/', 1)[-1]}:{f.lineno} {f.name}"
            for f in frames
        ],
    }


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_secrets,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=False,
    )
