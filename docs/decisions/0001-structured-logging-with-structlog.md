# 0001 - Structured JSON logging with structlog, standard-library logs included

**Status:** Accepted, 2026-09-30
**Applies to:** `mtg_deck_advisor.observability`

## Context

OBS-1 and OBS-2 require that any single request can be reconstructed end to end from its trace ID: every model call, tool call and log line of that request must be findable by one ID. Two things follow:

- **Logs must be machine-readable.** Reconstructing a request means filtering log lines by `trace_id`, which needs structured fields, not a message to parse with a regex.
- **Third-party logs count too.** uvicorn, alembic and httpx log through Python's standard `logging` module. If their lines come out in a different format, or without the trace ID, a request's log has holes exactly where the database or an HTTP call misbehaved.

The trace ID itself has to follow a request through `async` code, where one process interleaves many requests on a single thread.

## Decision

- **structlog**, configured so the standard library's root handler uses structlog's `ProcessorFormatter`. Lines from structlog loggers and standard-library loggers go through the same processors (log level, logger name, UTC ISO timestamp, trace ID) and the same renderer.
- **One JSON object per line on stdout** (`LOG_FORMAT=json`, the default). A readable console renderer (`LOG_FORMAT=console`) is for local terminals only.
- **The trace ID lives in a `ContextVar`.** A processor adds it to every line logged inside a `traced()` block. asyncio gives each task its own copy of the context, so concurrent requests never see each other's ID. A unit test checks this with two interleaved tasks.
- **Logs go to stdout.** Where they end up (a file, a log service, a data warehouse for DEP-4) is the platform's job.

## Alternatives

**Standard library only, with a JSON formatter** (e.g. `python-json-logger`). One fewer dependency. But adding context means `extra={...}` dictionaries at every call site or a custom `LoggerAdapter`, and the processor pipeline (adding the trace ID, timestamps, exception formatting) would be rebuilt by hand. structlog's `logger.info("card_loaded", card=name)` keeps call sites short and fields consistent.

**structlog alone, without routing the standard library through it.** Simpler configuration, but uvicorn's and alembic's lines would be plain text without trace IDs. That's the hole described in Context.

**Taking the trace ID from an incoming request header.** Considered for the API middleware (milestone 0, issue #16) and rejected there: request headers are untrusted input (GRD-3), and a client-chosen ID could collide with or impersonate another request's trace. The server always generates the ID and returns it in a response header.

## Consequences

- One format and one trace ID across the whole log, including third-party libraries.
- JSON lines are awkward to read raw in a terminal. That's what `LOG_FORMAT=console` is for.
- `configure_logging()` replaces the root logger's handlers, so it must run once at process start, before anything logs. Tests that call it restore the previous state afterwards.
- A trace ID only reaches work that runs inside a `traced()` block, or in a task started from one (tasks inherit the context). Work handed to a thread pool without copying the context would lose it. That becomes relevant once model calls run in threads.
