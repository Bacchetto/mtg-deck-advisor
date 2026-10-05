"""Structured logging: one JSON object per line, each carrying the trace ID (OBS-1, OBS-2).

structlog formats every line, including lines from standard-library loggers
used by third-party code (uvicorn, alembic, httpx), so a single format and a
single trace ID run through the whole log. See ADR 0001.
"""

import logging
import sys
from typing import Any, TextIO

import structlog
from structlog.typing import EventDict, Processor

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.observability.tracing import current_trace_id


def add_trace_id(_logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
    """structlog processor: add the current trace ID, if there is one."""
    trace_id = current_trace_id()
    if trace_id is not None:
        event_dict.setdefault("trace_id", trace_id)
    return event_dict


def configure_logging(settings: Settings, stream: TextIO | None = None) -> None:
    """Route structlog and standard-library logging through one formatter on stdout.

    Call once at process start. Logs go to stdout because containers and CI
    collect a process's output; where they end up is the platform's job. A
    process whose stdout is a protocol (the MCP server over stdio) passes
    `stream=sys.stderr` instead.
    """
    # Applied to every line, whether it came from structlog or the standard
    # library, before rendering.
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_trace_id,
    ]

    renderer: Processor
    if settings.log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    # structlog loggers hand their events to the standard library, so both
    # kinds of logger share the handler, the level filter and the renderer
    # configured below.
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # Standard-library records have not been through `shared` yet.
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)
