"""Per-request trace IDs for the HTTP API (OBS-2).

A plain ASGI middleware rather than Starlette's BaseHTTPMiddleware: it wraps
the whole request, including the response body, in one `traced()` block, and
it adds no extra task between the server and the app for the context to get
lost in.
"""

import time

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mtg_deck_advisor.observability.tracing import traced

TRACE_HEADER = "X-Trace-Id"

log = structlog.get_logger(__name__)


class TraceMiddleware:
    """Give every HTTP request a fresh trace ID, return it, and log the request.

    Any trace ID the client sends is ignored: request headers are untrusted
    input (GRD-3), and a client-chosen ID could collide with or impersonate
    another request's trace. See ADR 0001.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        with traced() as trace_id:
            started = time.perf_counter()
            status_code = 500

            async def send_with_trace_id(message: Message) -> None:
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = message["status"]
                    MutableHeaders(scope=message).append(TRACE_HEADER, trace_id)
                await send(message)

            try:
                await self.app(scope, receive, send_with_trace_id)
            finally:
                # One line per request, in place of the server's access log,
                # which is written outside the request and so has no trace ID.
                log.info(
                    "request_finished",
                    method=scope["method"],
                    path=scope["path"],
                    status_code=status_code,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )
