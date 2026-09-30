"""Trace IDs that link every log line, model call and tool call of one request (OBS-2).

The current trace ID lives in a context variable rather than a global or a
thread-local. Each asyncio task gets its own copy of the context, so two
requests handled concurrently in one process never see each other's ID.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    """A fresh random trace ID: 32 lowercase hex characters."""
    return uuid.uuid4().hex


def current_trace_id() -> str | None:
    """The trace ID of the work in progress, or None outside any trace."""
    return _trace_id.get()


@contextmanager
def traced(trace_id: str | None = None) -> Iterator[str]:
    """Run a block under a trace ID (a new one unless given), then restore the previous one."""
    trace_id = trace_id or new_trace_id()
    token = _trace_id.set(trace_id)
    try:
        yield trace_id
    finally:
        _trace_id.reset(token)
