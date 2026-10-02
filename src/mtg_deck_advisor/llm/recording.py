"""Recording every model call: what was asked, what came back, what it cost (OBS-1, OBS-2).

Each provider call becomes one record, retries included, so a request can be
reconstructed end to end from its trace ID.
"""

from typing import Any, Literal, Protocol

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect

Outcome = Literal["ok", "error", "refusal", "invalid_output", "budget_exceeded"]


class CallRecord(BaseModel):
    trace_id: str | None
    purpose: str
    provider: str
    model: str
    # 1 for the first try, 2 for a retry after invalid output.
    attempt: int
    outcome: Outcome
    request: dict[str, Any]
    response_text: str | None = None
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float | None = None
    error: str | None = None


class CallRecorder(Protocol):
    def record(self, call: CallRecord) -> None: ...


class MemoryRecorder:
    """Keeps records in a list, for tests."""

    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    def record(self, call: CallRecord) -> None:
        self.records.append(call)


class DatabaseRecorder:
    """Writes each record to the model_calls table, in its own short transaction."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def record(self, call: CallRecord) -> None:
        with connect(self._settings) as conn:
            conn.execute(
                """
                INSERT INTO model_calls (
                    trace_id, purpose, provider, model, attempt, outcome, request,
                    response_text, stop_reason, input_tokens, output_tokens,
                    cache_read_input_tokens, cache_creation_input_tokens,
                    cost_usd, latency_ms, error
                ) VALUES (
                    %(trace_id)s, %(purpose)s, %(provider)s, %(model)s, %(attempt)s,
                    %(outcome)s, %(request)s, %(response_text)s, %(stop_reason)s,
                    %(input_tokens)s, %(output_tokens)s, %(cache_read_input_tokens)s,
                    %(cache_creation_input_tokens)s, %(cost_usd)s, %(latency_ms)s, %(error)s
                )
                """,
                call.model_dump() | {"request": Jsonb(call.request)},
            )
