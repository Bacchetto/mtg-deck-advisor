"""Fakes for tests: a scripted provider, and an embedder that needs no model."""

import hashlib
import math
import re
from collections.abc import Sequence

from mtg_deck_advisor.llm.types import ProviderRequest, ProviderResponse


class FakeEmbedder:
    """Deterministic embeddings from word hashing, so retrieval runs in tests and CI.

    Each word adds +1 or -1 to one of the vector's dimensions, chosen by a
    hash of the word, and the result is normalised. Texts that share words
    point the same way, so searches over real pgvector indexes behave
    sensibly: like keyword matching, with no notion of meaning.
    """

    def __init__(self, model: str = "fake-embedder", dimensions: int = 1024) -> None:
        self._model = model
        self._dimensions = dimensions
        self.calls: list[list[str]] = []

    @property
    def model(self) -> str:
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for word in re.findall(r"\w+", text.lower()):
            digest = int.from_bytes(hashlib.sha256(word.encode()).digest()[:8], "big")
            vector[digest % self._dimensions] += 1.0 if digest & (1 << 63) else -1.0
        norm = math.sqrt(sum(x * x for x in vector))
        if norm == 0:
            vector[0], norm = 1.0, 1.0
        return [x / norm for x in vector]


class FakeProvider:
    def __init__(self, *replies: ProviderResponse | Exception, bills: bool = True) -> None:
        self._replies = list(replies)
        self._bills = bills
        self.calls: list[ProviderRequest] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def bills(self) -> bool:
        return self._bills

    def complete(self, request: ProviderRequest, model: str) -> ProviderResponse:
        self.calls.append(request)
        if not self._replies:
            raise AssertionError("FakeProvider ran out of scripted replies")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
