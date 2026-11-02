"""Structured JSON logging with secret redaction."""

import logging
from typing import Any

import structlog
from structlog.types import EventDict

_SENSITIVE = ("secret", "token", "password", "api_key", "apikey", "authorization", "credential")
REDACTED = "[REDACTED]"


def redact_secrets(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    for key in list(event_dict):
        if any(s in key.lower() for s in _SENSITIVE):
            event_dict[key] = REDACTED
    return event_dict


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
