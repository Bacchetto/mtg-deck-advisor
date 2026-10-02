"""ModelClient: the one way the app calls a model (MOD-1). See ADR 0008.

It wraps whichever provider is configured and adds what every call needs:
- a cost cap, checked against each call's worst case before it is made (MOD-3)
- structured output validated against a Pydantic schema, retried once with the
  validation errors, then rejected; invalid output is never passed on (MOD-2)
- a record of every provider call: tokens, cost, latency, outcome, trace ID (OBS-1)
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError

# Re-exported so callers can import every model error from the client.
from mtg_deck_advisor.llm.errors import BudgetExceededError as BudgetExceededError
from mtg_deck_advisor.llm.errors import InvalidOutputError as InvalidOutputError
from mtg_deck_advisor.llm.errors import ModelCallError as ModelCallError
from mtg_deck_advisor.llm.errors import ModelError as ModelError
from mtg_deck_advisor.llm.errors import RefusalError as RefusalError
from mtg_deck_advisor.llm.pricing import PRICES, cost_usd, worst_case_cost_usd
from mtg_deck_advisor.llm.recording import CallRecord, CallRecorder, Outcome
from mtg_deck_advisor.llm.types import Message, ModelRequest, Provider, ProviderRequest, Usage
from mtg_deck_advisor.observability.tracing import current_trace_id

log = structlog.get_logger(__name__)

# A rough characters-per-token ratio, used only to estimate a call's input
# size for the budget check before the call is made.
CHARS_PER_TOKEN = 4


class ModelResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    stop_reason: str
    usage: Usage
    cost_usd: float
    latency_ms: float
    provider: str
    model: str


@dataclass(frozen=True)
class Structured[T: BaseModel]:
    """A validated structured result, with the response it came from."""

    value: T
    response: ModelResponse
    attempts: int


class ModelClient:
    def __init__(
        self,
        provider: Provider,
        model: str,
        *,
        recorder: CallRecorder,
        cost_cap_usd: float | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if provider.bills and model not in PRICES:
            raise ValueError(f"no price for model {model!r}: add it to llm.pricing.PRICES")
        self._provider = provider
        self._model = model
        self._recorder = recorder
        self._cost_cap_usd = cost_cap_usd
        self._clock = clock
        self._spent_usd = 0.0
        # Worst-case cost of calls in flight. A call's worst case is reserved
        # before it is made and released when it finishes, all under one lock,
        # so concurrent calls can't each pass the cap check and jointly break it.
        self._reserved_usd = 0.0
        self._lock = threading.Lock()

    @property
    def model(self) -> str:
        return self._model

    @property
    def remaining_usd(self) -> float | None:
        """What can still be spent under the cap; None when nothing is capped or billed."""
        if self._cost_cap_usd is None or not self._provider.bills:
            return None
        with self._lock:
            return self._cost_cap_usd - self._spent_usd - self._reserved_usd

    @property
    def spent_usd(self) -> float:
        """What this client's calls have cost so far."""
        return self._spent_usd

    def generate(self, request: ModelRequest) -> ModelResponse:
        """A plain text response."""
        provider_request = ProviderRequest(**request.model_dump())
        response = self._invoke(provider_request, attempt=1)
        self._record(provider_request, 1, "ok", response)
        return response

    def generate_structured[T: BaseModel](
        self, request: ModelRequest, schema: type[T]
    ) -> Structured[T]:
        """A response parsed and validated as `schema`, retried once if it doesn't fit."""
        current = ProviderRequest(**request.model_dump(), output_schema=schema.model_json_schema())
        for attempt in (1, 2):
            response = self._invoke(current, attempt)
            try:
                value = schema.model_validate_json(response.text)
            except ValidationError as exc:
                self._record(current, attempt, "invalid_output", response, error=str(exc))
                if attempt == 2:
                    raise InvalidOutputError(
                        f"{request.purpose}: output still invalid after a retry: {exc}"
                    ) from exc
                current = _with_correction(current, response.text, exc)
                continue
            self._record(current, attempt, "ok", response)
            return Structured(value=value, response=response, attempts=attempt)
        raise AssertionError("unreachable")  # pragma: no cover

    def _invoke(self, request: ProviderRequest, attempt: int) -> ModelResponse:
        """One provider call, with the budget check before it and cost accounting after."""
        reservation = self._reserve(request)
        if reservation is None:
            self._record(request, attempt, "budget_exceeded", None)
            raise BudgetExceededError(
                f"{request.purpose}: could exceed the ${self._cost_cap_usd:.2f} cost cap "
                f"(${self._spent_usd:.4f} spent so far)"
            )

        started = self._clock()
        try:
            reply = self._provider.complete(request, self._model)
        except ModelError as exc:
            # Already specific (a spend limit, bad credentials): keep its type.
            self._settle(reservation, 0.0)
            self._record(request, attempt, "error", None, error=str(exc))
            raise
        except Exception as exc:
            self._settle(reservation, 0.0)
            self._record(request, attempt, "error", None, error=str(exc))
            raise ModelCallError(f"{self._provider.name} call failed: {exc}") from exc
        latency_ms = (self._clock() - started) * 1000

        cost = self._cost(reply.model, reply.usage) if self._provider.bills else 0.0
        self._settle(reservation, cost)
        response = ModelResponse(
            text=reply.text,
            stop_reason=reply.stop_reason,
            usage=reply.usage,
            cost_usd=cost,
            latency_ms=latency_ms,
            provider=self._provider.name,
            model=reply.model,
        )
        if reply.stop_reason == "refusal":
            self._record(request, attempt, "refusal", response)
            raise RefusalError(f"{request.purpose}: the model declined to answer")
        return response

    def _cost(self, answered_by: str, usage: Usage) -> float:
        """The call's cost, at the price of the model that actually answered.

        A server-side refusal fallback can answer from a model the price table
        lacks. The money is already spent, so rather than fail, the call is
        costed at the requested model's price, with a warning to add the model.
        """
        if answered_by in PRICES:
            return cost_usd(answered_by, usage)
        log.warning("model_price_unknown", model=answered_by, costed_as=self._model)
        return cost_usd(self._model, usage)

    def _worst_case_usd(self, request: ProviderRequest) -> float:
        chars = len(request.system) + sum(len(m.content) for m in request.messages)
        if request.output_schema is not None:
            chars += len(str(request.output_schema))
        return worst_case_cost_usd(self._model, chars // CHARS_PER_TOKEN, request.max_tokens)

    def _reserve(self, request: ProviderRequest) -> float | None:
        """Reserve the call's worst case against the cap, or None if it doesn't fit."""
        if self._cost_cap_usd is None or not self._provider.bills:
            return 0.0
        worst = self._worst_case_usd(request)
        with self._lock:
            if self._spent_usd + self._reserved_usd + worst > self._cost_cap_usd:
                return None
            self._reserved_usd += worst
            return worst

    def _settle(self, reservation: float, cost: float) -> None:
        """Replace a call's reservation with what it actually cost."""
        with self._lock:
            self._reserved_usd -= reservation
            self._spent_usd += cost

    def _record(
        self,
        request: ProviderRequest,
        attempt: int,
        outcome: Outcome,
        response: ModelResponse | None,
        *,
        error: str | None = None,
    ) -> None:
        usage = response.usage if response else Usage()
        record = CallRecord(
            trace_id=current_trace_id(),
            purpose=request.purpose,
            provider=self._provider.name,
            model=response.model if response else self._model,
            attempt=attempt,
            outcome=outcome,
            request=request.model_dump(mode="json"),
            response_text=response.text if response else None,
            stop_reason=response.stop_reason if response else None,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            cost_usd=response.cost_usd if response else 0.0,
            latency_ms=response.latency_ms if response else None,
            error=error,
        )
        log.info(
            "model_call",
            purpose=record.purpose,
            provider=record.provider,
            model=record.model,
            attempt=attempt,
            outcome=outcome,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cost_usd=round(record.cost_usd, 6),
            latency_ms=round(record.latency_ms, 1) if record.latency_ms is not None else None,
        )
        self._recorder.record(record)


def _with_correction(
    request: ProviderRequest, invalid: str, exc: ValidationError
) -> ProviderRequest:
    """The same request, now showing the model its invalid answer and what was wrong."""
    problems = "\n".join(
        f"- {'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors()
    )
    correction = (
        "Your reply did not match the required JSON schema:\n"
        f"{problems}\n"
        "Reply again with only JSON that matches the schema."
    )
    messages: tuple[Message, ...] = (
        *request.messages,
        Message(role="assistant", content=invalid),
        Message(role="user", content=correction),
    )
    update: dict[str, Any] = {"messages": messages}
    return request.model_copy(update=update)
