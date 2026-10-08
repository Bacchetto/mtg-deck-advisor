"""Recorded model responses: record from a real provider, replay with no network (DEM-2).

Two uses:
- a demo that works without an API key or a local model, on a clean clone
- deterministic evals in CI (EVL-5), where the same request must get the same
  answer, at no cost

Each recording is one JSON file named by the request's key, in a committed
directory (`REPLAY_DIR`). One file per request keeps recordings readable,
makes them diff cleanly in review, and means two branches that record
different requests never conflict.

Search needs two more models, which replay the same way: the embedding
model, whose query vectors are saved under `REPLAY_DIR/embeddings/`, and the
reranker, an ordinary model call. A replayed search then returns exactly
what it returned when recorded, so the agent's next request, which carries
that result, finds its recording too.
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from mtg_deck_advisor.llm.errors import ModelCallError
from mtg_deck_advisor.llm.ollama import Embedder
from mtg_deck_advisor.llm.types import (
    Provider,
    ProviderRequest,
    ProviderResponse,
    request_dump,
)


class ReplayMissError(ModelCallError):
    """No recording exists for this request."""


def request_key(request: ProviderRequest, model: str) -> str:
    """A stable hash of everything that could change the model's answer.

    The purpose label is left out: it describes the call, it doesn't shape the
    answer, and renaming it must not invalidate the recordings made under it.
    """
    # Empty tool fields are left out, so existing recordings still replay.
    content = request_dump(request, exclude={"purpose"})
    content["model"] = model
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _path(directory: Path, request: ProviderRequest, model: str) -> Path:
    return directory / f"{request_key(request, model)}.json"


class RecordingProvider:
    """Wraps a real provider, saving every response it returns.

    It keeps the real provider's name and billing, so recorded calls are logged
    and costed like any other real call.
    """

    def __init__(self, inner: Provider, directory: Path) -> None:
        self._inner = inner
        self._directory = directory

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def bills(self) -> bool:
        return self._inner.bills

    def complete(self, request: ProviderRequest, model: str) -> ProviderResponse:
        response = self._inner.complete(request, model)
        self._directory.mkdir(parents=True, exist_ok=True)
        recording = {
            "request_hash": request_key(request, model),
            "model": model,
            "recorded_from": self._inner.name,
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "request": request.model_dump(mode="json", exclude={"purpose"}),
            "response": response.model_dump(mode="json"),
        }
        _path(self._directory, request, model).write_text(
            json.dumps(recording, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return response


class ReplayProvider:
    """Serves recorded responses. Free, and makes no network calls."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def name(self) -> str:
        return "replay"

    @property
    def bills(self) -> bool:
        return False

    def complete(self, request: ProviderRequest, model: str) -> ProviderResponse:
        path = _path(self._directory, request, model)
        if not path.exists():
            raise ReplayMissError(
                f"no recording for this {request.purpose!r} request to {model} in "
                f"{self._directory}. Record it by running once with RECORD_RESPONSES=true "
                f"and a real MODEL_PROVIDER."
            )
        recording = json.loads(path.read_text(encoding="utf-8"))
        return ProviderResponse.model_validate(recording["response"])


# --- embeddings ----------------------------------------------------------------------

EMBEDDINGS_DIR = "embeddings"


def _embedding_path(directory: Path, model: str, text: str) -> Path:
    canonical = json.dumps({"model": model, "text": text}, sort_keys=True, ensure_ascii=False)
    key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return directory / EMBEDDINGS_DIR / f"{key}.json"


class RecordingEmbedder:
    """Wraps a real embedder, saving each text's vector, in full, under `embeddings/`."""

    def __init__(self, inner: Embedder, directory: Path) -> None:
        self._inner = inner
        self._directory = directory

    @property
    def model(self) -> str:
        return self._inner.model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._inner.embed(texts)
        (self._directory / EMBEDDINGS_DIR).mkdir(parents=True, exist_ok=True)
        for text, vector in zip(texts, vectors, strict=True):
            recording = {"model": self.model, "text": text, "embedding": vector}
            _embedding_path(self._directory, self.model, text).write_text(
                json.dumps(recording, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        return vectors


class ReplayEmbedder:
    """Serves recorded vectors. Free, and needs no embedding model."""

    def __init__(self, directory: Path, model: str) -> None:
        self._directory = directory
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            path = _embedding_path(self._directory, self._model, text)
            if not path.exists():
                raise ReplayMissError(
                    f"no recorded embedding from {self._model} for {text[-80:]!r} in "
                    f"{self._directory / EMBEDDINGS_DIR}. Record it by running once with "
                    "RECORD_RESPONSES=true and a real MODEL_PROVIDER."
                )
            vectors.append(json.loads(path.read_text(encoding="utf-8"))["embedding"])
        return vectors
