"""Structured logging setup (Doc 01 section 1.8.2).

JSON lines in production-like profiles, human-readable colour in local dev.
Every log line carries the bound context variables (trace_id, unit_id, run_id).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(
    *,
    service_name: str,
    level: str = "INFO",
    json_output: bool = True,
) -> None:
    """Configure structlog and the stdlib logging bridge exactly once per process."""
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _add_service(service_name),
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, sqlalchemy) through the same renderer.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.getLevelNamesMapping()[level],
        force=True,
    )
    for noisy in ("uvicorn.access", "sqlalchemy.engine.Engine"):
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True


def _add_service(service_name: str) -> Any:
    """Processor factory that stamps every event with the emitting service."""

    def processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        event_dict["service"] = service_name
        return event_dict

    return processor


def get_logger(name: str) -> Any:
    """Convenience accessor mirroring ``logging.getLogger``."""
    return structlog.get_logger(name)
