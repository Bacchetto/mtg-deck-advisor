"""Reranking card search candidates with a local model (#75, #76).

The first stage (hybrid search) finds candidates by similarity; a model then
reads the top 20 cards' real rules text and orders them by how well they
answer the search. On the retrieval dev set, `qwen3:8b` doing this lifted
card MRR from 0.64 to 0.83 (the right card is usually first or second), at a
median 1.3 s a search.

`qwen3:8b` was chosen over `qwen3:14b` (MRR 0.87) because the 14b and the 8b
embedding model don't fit in a 16 GB GPU together: the overflow spills into
system RAM and a rerank took 9 s instead of 1.4 s. Rules searches aren't
reranked: they already find the answering rule in the top 10 every time.

Search must never depend on the reranker: on any failure (Ollama down, the
model not pulled, an unreadable answer) the first stage's order stands.
"""

from collections.abc import Hashable, Sequence
from typing import Protocol

import structlog
from pydantic import BaseModel

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.errors import ModelError
from mtg_deck_advisor.llm.ollama import OllamaProvider
from mtg_deck_advisor.llm.recording import DatabaseRecorder
from mtg_deck_advisor.llm.replay import RecordingProvider, ReplayMissError, ReplayProvider
from mtg_deck_advisor.llm.types import Message, ModelRequest, Provider

log = structlog.get_logger(__name__)

# How many first-stage candidates the model reads.
RERANK_DEPTH = 20
SYSTEM_PROMPT = (
    "You rank search results for a Magic: The Gathering deck-building assistant. Given a "
    "search and numbered candidates (cards or Comprehensive Rules), return the numbers of the "
    "10 candidates that best answer the search, best first. Judge by what each candidate "
    "actually does or says, not by shared words."
)


class Reranker[K: Hashable](Protocol):
    def rerank(self, query: str, candidates: Sequence[tuple[K, str]]) -> list[K]:
        """The candidates' keys, best first: every key, each exactly once."""
        ...


class Ranking(BaseModel):
    ranking: list[int]


class LlmReranker:
    """Reranks (key, text) candidates with a chat model, keeping the order on any failure."""

    def __init__(self, client: ModelClient) -> None:
        self._client = client

    @property
    def model(self) -> str:
        return self._client.model

    def rerank[K: Hashable](self, query: str, candidates: Sequence[tuple[K, str]]) -> list[K]:
        keys = [key for key, _ in candidates]
        if not keys:
            return []
        listing = "\n".join(
            f"{number}. {text.replace(chr(10), ' / ')}"
            for number, (_, text) in enumerate(candidates, start=1)
        )
        request = ModelRequest(
            purpose="rerank",
            system=SYSTEM_PROMPT,
            messages=(Message(role="user", content=f"Search: {query}\n\nCandidates:\n{listing}"),),
            max_tokens=200,
            effort="low",
        )
        try:
            ranking = self._client.generate_structured(request, Ranking).value.ranking
        except ReplayMissError:
            # Not a model failure: a replay without this recording can't go on.
            raise
        except ModelError as exc:
            log.warning("rerank_failed", error=str(exc), candidates=len(keys))
            return keys
        chosen = list(dict.fromkeys(n for n in ranking if 1 <= n <= len(keys)))
        ranked = [keys[number - 1] for number in chosen]
        placed = set(ranked)
        return ranked + [key for key in keys if key not in placed]


def build_reranker(settings: Settings) -> LlmReranker | None:
    """The configured reranker (RERANK_MODEL through Ollama), or None when it's turned off.

    Like the embedder, it follows the replay settings: MODEL_PROVIDER=replay
    serves recorded rankings, and RECORD_RESPONSES saves them.
    """
    if not settings.rerank_model:
        return None
    provider: Provider
    if settings.model_provider == "replay":
        provider = ReplayProvider(settings.replay_dir)
    else:
        provider = OllamaProvider(
            base_url=settings.ollama_base_url, timeout_seconds=settings.ollama_timeout_seconds
        )
        if settings.record_responses:
            provider = RecordingProvider(provider, settings.replay_dir)
    client = ModelClient(provider, settings.rerank_model, recorder=DatabaseRecorder(settings))
    return LlmReranker(client)
