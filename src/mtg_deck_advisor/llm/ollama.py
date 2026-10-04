"""A local model, and local embeddings, through Ollama's HTTP API (MOD-4). See ADR 0009.

Ollama runs natively on the host, so it can use the GPU; the app reaches it on
port 11434. Local calls cost nothing, so they aren't priced or budgeted.

Unlike a cloud API, a local server that isn't running, or a model that isn't
pulled, won't recover in a few seconds, so there are no retries: failures are
reported at once, with what to do about them.

The one exception is Windows running out of short-lived network ports during
long runs ("lacked sufficient buffer space"): Ollama makes an internal HTTP
call per text, and the ports free up within a minute or two. That's waited
out and retried a few times (#76).
"""

import math
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import httpx2

from mtg_deck_advisor.llm.errors import ModelCallError
from mtg_deck_advisor.llm.types import ProviderRequest, ProviderResponse, Usage

# Effort levels at which a thinking model (such as Qwen3) is allowed to think.
# Below them thinking is turned off: it would spend the output budget on
# reasoning that a classification doesn't need.
THINKING_EFFORTS = frozenset({"high", "xhigh", "max"})

# Ollama's done_reason, in the stop_reason vocabulary the rest of the app uses.
STOP_REASONS = {"stop": "end_turn", "length": "max_tokens"}


class Embedder(Protocol):
    """Turns texts into vectors, in the same order."""

    @property
    def model(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


# Windows' error when it has run out of short-lived network ports.
PORTS_EXHAUSTED = "lacked sufficient buffer space"
PORT_WAIT_SECONDS = 60.0
PORT_ATTEMPTS = 5


class _OllamaHttp:
    """Shared HTTP handling: one client, and Ollama failures turned into clear errors."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        http_client: httpx2.Client | None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http_client or httpx2.Client(timeout=timeout_seconds)
        self._sleep = sleep

    def _post(self, path: str, body: dict[str, Any], model: str) -> dict[str, Any]:
        for attempt in range(1, PORT_ATTEMPTS + 1):
            try:
                return self._post_once(path, body, model)
            except ModelCallError as exc:
                if PORTS_EXHAUSTED not in str(exc):
                    raise
                if attempt == PORT_ATTEMPTS:
                    raise ModelCallError(
                        f"Windows ran out of network ports for Ollama, and it persisted over "
                        f"{PORT_ATTEMPTS} attempts {PORT_WAIT_SECONDS:.0f} s apart: {exc}"
                    ) from exc
                self._sleep(PORT_WAIT_SECONDS)
        raise AssertionError("unreachable")  # pragma: no cover

    def _post_once(self, path: str, body: dict[str, Any], model: str) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            response = self._http.post(url, json=body)
        except httpx2.ConnectError as exc:
            raise ModelCallError(
                f"cannot reach Ollama at {self._base_url}. Is Ollama running? ({exc})"
            ) from exc
        except httpx2.TimeoutException as exc:
            raise ModelCallError(
                f"Ollama took too long to answer (a first load of a large model can be slow; "
                f"OLLAMA_TIMEOUT_SECONDS raises the limit): {exc}"
            ) from exc
        if response.status_code == 404:
            raise ModelCallError(
                f"Ollama doesn't have the model {model!r}. Run: ollama pull {model}"
            )
        if response.status_code >= 400:
            raise ModelCallError(f"Ollama returned HTTP {response.status_code}: {response.text}")
        result: dict[str, Any] = response.json()
        return result


class OllamaProvider(_OllamaHttp):
    def __init__(
        self, *, base_url: str, timeout_seconds: float, http_client: httpx2.Client | None = None
    ) -> None:
        super().__init__(base_url, timeout_seconds, http_client)

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def bills(self) -> bool:
        return False

    def complete(self, request: ProviderRequest, model: str) -> ProviderResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system},
                *(message.model_dump() for message in request.messages),
            ],
            "stream": False,
            "think": request.effort in THINKING_EFFORTS,
            # Temperature 0: the same card gets the same tags, run to run.
            "options": {"num_predict": request.max_tokens, "temperature": 0},
        }
        if request.output_schema is not None:
            body["format"] = request.output_schema

        reply = self._post("/api/chat", body, model)
        done_reason = reply.get("done_reason", "")
        return ProviderResponse(
            text=reply.get("message", {}).get("content", ""),
            stop_reason=STOP_REASONS.get(done_reason, done_reason),
            model=reply.get("model", model),
            usage=Usage(
                input_tokens=reply.get("prompt_eval_count", 0),
                output_tokens=reply.get("eval_count", 0),
            ),
        )


class OllamaEmbedder(_OllamaHttp):
    """Embeddings from a local Ollama model, normalised (length 1).

    With `dimensions`, each vector is cut to its first `dimensions` values and
    renormalised. Qwen3 embedding models are Matryoshka-trained, so the leading
    values form a valid smaller embedding: the 8b model's 4,096 become the
    schema's 1,024, which pgvector's HNSW index can hold (its limit is 2,000).
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        dimensions: int | None = None,
        batch_size: int = 64,
        http_client: httpx2.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(base_url, timeout_seconds, http_client, sleep)
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size

    @property
    def model(self) -> str:
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            reply = self._post("/api/embed", {"model": self._model, "input": batch}, self._model)
            embeddings: list[list[float]] = reply.get("embeddings", [])
            if len(embeddings) != len(batch):
                raise ModelCallError(
                    f"Ollama returned {len(embeddings)} embeddings for {len(batch)} texts"
                )
            vectors.extend(self._truncate(vector) for vector in embeddings)
        return vectors

    def _truncate(self, vector: list[float]) -> list[float]:
        if self._dimensions is None:
            return vector
        if len(vector) < self._dimensions:
            raise ModelCallError(
                f"{self._model} returned {len(vector)} dimensions; {self._dimensions} were "
                "configured (EMBEDDING_DIMENSIONS)"
            )
        cut = vector[: self._dimensions]
        norm = math.sqrt(sum(x * x for x in cut))
        return [x / norm for x in cut]
