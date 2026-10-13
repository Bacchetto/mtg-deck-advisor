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

import json
import math
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import httpx2

from mtg_deck_advisor.llm.errors import ModelCallError
from mtg_deck_advisor.llm.types import ProviderRequest, ProviderResponse, ToolCall, Usage

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


def _inlined(schema: Any, defs: dict[str, Any] | None = None) -> Any:
    """A JSON schema with each `$ref` replaced by what it points to in `$defs`.

    Pydantic puts nested models under `$defs`. A local model reads the schema as
    text, and may not follow a reference.
    """
    if isinstance(schema, list):
        return [_inlined(item, defs) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if defs is None:
        defs = schema.get("$defs", {})
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        return _inlined(defs.get(ref.removeprefix("#/$defs/"), {}), defs)
    return {key: _inlined(value, defs) for key, value in schema.items() if key != "$defs"}


def _chat_messages(request: ProviderRequest) -> list[dict[str, Any]]:
    """The conversation as Ollama's /api/chat takes it.

    A tool result names the tool it came from, which the project's ToolResult
    doesn't carry: it's looked up from the call that asked for it.
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": request.system}]
    names: dict[str, str] = {}
    for message in request.messages:
        if message.role == "assistant":
            turn: dict[str, Any] = {"role": "assistant", "content": message.content}
            if message.tool_calls:
                turn["tool_calls"] = [
                    {"id": call.id, "function": {"name": call.name, "arguments": call.arguments}}
                    for call in message.tool_calls
                ]
                names |= {call.id: call.name for call in message.tool_calls}
            messages.append(turn)
            continue
        for result in message.tool_results:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": result.tool_call_id,
                    "tool_name": names.get(result.tool_call_id, ""),
                    "content": result.content,
                }
            )
        if message.content or not message.tool_results:
            messages.append({"role": "user", "content": message.content})
    return messages


def _tool_calls(reply: dict[str, Any]) -> tuple[ToolCall, ...]:
    calls = []
    for number, call in enumerate(reply.get("message", {}).get("tool_calls") or [], 1):
        function = call.get("function", {})
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):  # some models send the arguments as JSON text
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        calls.append(
            ToolCall(
                # Older Ollama versions give a call no id.
                id=call.get("id") or f"call_{number}",
                name=function.get("name", ""),
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )
    return tuple(calls)


class OllamaProvider(_OllamaHttp):
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        http_client: httpx2.Client | None = None,
        agent_num_ctx: int | None = None,
    ) -> None:
        super().__init__(base_url, timeout_seconds, http_client)
        # The context length to ask for when a request uses tools (an agent run).
        self._agent_num_ctx = agent_num_ctx

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def bills(self) -> bool:
        return False

    def complete(self, request: ProviderRequest, model: str) -> ProviderResponse:
        agent = bool(request.tools) or any(m.tool_calls or m.tool_results for m in request.messages)
        body: dict[str, Any] = {
            "model": model,
            "messages": _chat_messages(request),
            "stream": False,
            "think": request.effort in THINKING_EFFORTS,
            # Temperature 0: the same card gets the same tags, run to run.
            "options": {"num_predict": request.max_tokens, "temperature": 0},
        }
        if request.output_schema is not None:
            body["format"] = request.output_schema
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": _inlined(tool.input_schema),
                    },
                }
                for tool in request.tools
            ]
        if agent and self._agent_num_ctx is not None:
            # Ollama cuts a prompt longer than its context silently, and its
            # default is a few thousand tokens: less than one agent turn.
            body["options"]["num_ctx"] = self._agent_num_ctx

        reply = self._post("/api/chat", body, model)
        done_reason = reply.get("done_reason", "")
        calls = _tool_calls(reply)
        return ProviderResponse(
            text=reply.get("message", {}).get("content", ""),
            stop_reason="tool_use" if calls else STOP_REASONS.get(done_reason, done_reason),
            model=reply.get("model", model),
            usage=Usage(
                input_tokens=reply.get("prompt_eval_count", 0),
                output_tokens=reply.get("eval_count", 0),
            ),
            tool_calls=calls,
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
