"""A local model, and local embeddings, through Ollama's HTTP API (MOD-4). See ADR 0009.

Ollama runs natively on the host, so it can use the GPU; the app reaches it on
port 11434. Local calls cost nothing, so they aren't priced or budgeted.

Unlike a cloud API, a local server that isn't running, or a model that isn't
pulled, won't recover in a few seconds, so there are no retries: failures are
reported at once, with what to do about them.
"""

from collections.abc import Sequence
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


class _OllamaHttp:
    """Shared HTTP handling: one client, and Ollama failures turned into clear errors."""

    def __init__(
        self, base_url: str, timeout_seconds: float, http_client: httpx2.Client | None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http_client or httpx2.Client(timeout=timeout_seconds)

    def _post(self, path: str, body: dict[str, Any], model: str) -> dict[str, Any]:
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
    """Embeddings from a local Ollama model. Vectors come back normalised (length 1)."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        batch_size: int = 64,
        http_client: httpx2.Client | None = None,
    ) -> None:
        super().__init__(base_url, timeout_seconds, http_client)
        self._model = model
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
            vectors.extend(embeddings)
        return vectors
