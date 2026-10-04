"""Reranking search candidates with a local model, falling back to the original order."""

import json
from uuid import UUID

import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.fake import FakeProvider
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.types import ProviderResponse, Usage
from mtg_deck_advisor.retrieval.rerank import LlmReranker, build_reranker

CANDIDATES = [(UUID(int=i), f"Card {i} | Artifact | Does thing {i}.") for i in range(1, 6)]
KEYS = [key for key, _ in CANDIDATES]


def answer(ranking: list[int]) -> ProviderResponse:
    return ProviderResponse(
        text=json.dumps({"ranking": ranking}),
        stop_reason="end_turn",
        usage=Usage(input_tokens=300, output_tokens=20),
        model="qwen3:8b",
    )


def reranker(*replies: ProviderResponse | Exception) -> tuple[LlmReranker, FakeProvider]:
    provider = FakeProvider(*replies, bills=False)
    client = ModelClient(provider, "qwen3:8b", recorder=MemoryRecorder())
    return LlmReranker(client), provider


def test_candidates_come_back_in_the_models_order() -> None:
    rerank, _ = reranker(answer([3, 1, 5, 2, 4]))

    assert rerank.rerank("cheap ramp", CANDIDATES) == [KEYS[2], KEYS[0], KEYS[4], KEYS[1], KEYS[3]]


def test_candidates_the_model_left_out_keep_their_order_after_the_ranked_ones() -> None:
    rerank, _ = reranker(answer([4, 2]))

    assert rerank.rerank("q", CANDIDATES) == [KEYS[3], KEYS[1], KEYS[0], KEYS[2], KEYS[4]]


def test_numbers_out_of_range_or_repeated_are_ignored() -> None:
    rerank, _ = reranker(answer([2, 9, 2, 0, 1]))

    assert rerank.rerank("q", CANDIDATES) == [KEYS[1], KEYS[0], KEYS[2], KEYS[3], KEYS[4]]


def test_a_model_failure_keeps_the_original_order() -> None:
    rerank, _ = reranker(ConnectionError("Ollama is down"))

    assert rerank.rerank("q", CANDIDATES) == KEYS


def test_an_unreadable_answer_keeps_the_original_order() -> None:
    not_json = ProviderResponse(
        text="I think card 3 is best.",
        stop_reason="end_turn",
        usage=Usage(input_tokens=300, output_tokens=8),
        model="qwen3:8b",
    )
    rerank, _ = reranker(not_json, not_json)  # the client retries invalid output once

    assert rerank.rerank("q", CANDIDATES) == KEYS


def test_the_request_shows_the_search_and_numbered_candidates() -> None:
    rerank, provider = reranker(answer([1]))

    rerank.rerank("destroy a creature", CANDIDATES[:2])

    content = provider.calls[0].messages[0].content
    assert "destroy a creature" in content
    assert "1. Card 1 | Artifact | Does thing 1." in content
    assert "2. Card 2" in content
    assert provider.calls[0].effort == "low"


def test_nothing_to_rerank_makes_no_call() -> None:
    rerank, provider = reranker()

    assert rerank.rerank("q", []) == []
    assert provider.calls == []


def test_reranking_is_on_by_default_with_qwen3_8b(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RERANK_MODEL", raising=False)
    settings = Settings(_env_file=None, database_url="postgresql://u:p@localhost/db")

    assert settings.rerank_model == "qwen3:8b"
    reranker_ = build_reranker(settings)
    assert reranker_ is not None and reranker_.model == "qwen3:8b"


def test_an_empty_rerank_model_turns_reranking_off() -> None:
    settings = Settings(
        _env_file=None, database_url="postgresql://u:p@localhost/db", rerank_model=""
    )

    assert build_reranker(settings) is None
