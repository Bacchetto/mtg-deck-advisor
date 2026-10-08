"""Recorded query embeddings, so search replays without Ollama (#140, DEM-2)."""

from pathlib import Path

import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.llm.factory import build_embedder
from mtg_deck_advisor.llm.fake import FakeEmbedder
from mtg_deck_advisor.llm.ollama import OllamaEmbedder, OllamaProvider
from mtg_deck_advisor.llm.replay import (
    RecordingEmbedder,
    RecordingProvider,
    ReplayEmbedder,
    ReplayMissError,
    ReplayProvider,
)
from mtg_deck_advisor.retrieval.rerank import build_reranker


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, database_url="postgresql://x:y@127.0.0.1/db", **values)  # type: ignore[arg-type]


def test_a_recorded_embedding_replays_exactly(tmp_path: Path) -> None:
    real = FakeEmbedder(model="qwen3-embedding:8b")
    recorded = RecordingEmbedder(real, tmp_path).embed(["cheap ramp", "board wipes"])

    replayed = ReplayEmbedder(tmp_path, "qwen3-embedding:8b").embed(["board wipes", "cheap ramp"])

    assert replayed == [recorded[1], recorded[0]]


def test_the_recording_embedder_keeps_the_real_models_name(tmp_path: Path) -> None:
    assert RecordingEmbedder(FakeEmbedder(model="m"), tmp_path).model == "m"
    assert ReplayEmbedder(tmp_path, "m").model == "m"


def test_an_unknown_query_fails_clearly(tmp_path: Path) -> None:
    RecordingEmbedder(FakeEmbedder(model="m"), tmp_path).embed(["cheap ramp"])

    with pytest.raises(ReplayMissError, match=r"no recorded embedding.*RECORD_RESPONSES"):
        ReplayEmbedder(tmp_path, "m").embed(["cheap ramp", "board wipes"])


def test_a_recording_from_another_model_doesnt_replay(tmp_path: Path) -> None:
    RecordingEmbedder(FakeEmbedder(model="old"), tmp_path).embed(["cheap ramp"])

    with pytest.raises(ReplayMissError):
        ReplayEmbedder(tmp_path, "new").embed(["cheap ramp"])


def test_replay_mode_builds_a_replaying_embedder(tmp_path: Path) -> None:
    embedder = build_embedder(settings(model_provider="replay", replay_dir=tmp_path))

    assert isinstance(embedder, ReplayEmbedder)
    assert embedder.model == "qwen3-embedding:8b"


def test_recording_wraps_the_ollama_embedder(tmp_path: Path) -> None:
    embedder = build_embedder(
        settings(model_provider="anthropic", record_responses=True, replay_dir=tmp_path)
    )

    assert isinstance(embedder, RecordingEmbedder)


def test_otherwise_the_embedder_is_ollama() -> None:
    assert isinstance(build_embedder(settings()), OllamaEmbedder)


def test_the_reranker_replays_in_replay_mode(tmp_path: Path) -> None:
    reranker = build_reranker(settings(model_provider="replay", replay_dir=tmp_path))

    assert reranker is not None
    assert isinstance(reranker._client._provider, ReplayProvider)


def test_the_reranker_records_when_recording(tmp_path: Path) -> None:
    reranker = build_reranker(
        settings(model_provider="anthropic", record_responses=True, replay_dir=tmp_path)
    )

    assert reranker is not None
    provider = reranker._client._provider
    assert isinstance(provider, RecordingProvider)
    assert isinstance(provider._inner, OllamaProvider)
