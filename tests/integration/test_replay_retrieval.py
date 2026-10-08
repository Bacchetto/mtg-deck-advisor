"""A recorded search replays exactly, with no embedding or reranking model (#140, DEM-2).

The demo is recorded against a seeded database and replayed against another
seeded the same way. Each agent call's recording is keyed by the whole
request, tool results included, so a replayed search must return exactly
what it returned when recorded.
"""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

import json
from pathlib import Path

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.factory import build_embedder
from mtg_deck_advisor.llm.fake import FakeEmbedder, FakeProvider
from mtg_deck_advisor.llm.recording import DatabaseRecorder
from mtg_deck_advisor.llm.replay import RecordingEmbedder, RecordingProvider
from mtg_deck_advisor.llm.types import ProviderResponse, Usage
from mtg_deck_advisor.retrieval.rerank import LlmReranker, build_reranker
from mtg_deck_advisor.retrieval.search import search_cards, search_rules
from tests.integration.test_agent_tools import loaded  # noqa: F401


def test_a_replayed_search_returns_exactly_what_was_recorded(
    loaded: Settings, tmp_path: Path
) -> None:
    recording = loaded.model_copy(update={"replay_dir": tmp_path, "rerank_model": "qwen3:8b"})
    # A reranker that reverses the first stage's order, so replay must use it.
    ranking = ProviderResponse(
        text=json.dumps({"ranking": [5, 4, 3, 2, 1]}),
        stop_reason="end_turn",
        usage=Usage(input_tokens=300, output_tokens=20),
        model="qwen3:8b",
    )
    real_reranker = LlmReranker(
        ModelClient(
            RecordingProvider(FakeProvider(ranking, bills=False), tmp_path),
            "qwen3:8b",
            recorder=DatabaseRecorder(recording),
        )
    )
    real_embedder = RecordingEmbedder(FakeEmbedder(model="qwen3-embedding:8b"), tmp_path)
    with connect(loaded) as conn:
        conn.execute("UPDATE card_embeddings SET model = 'qwen3-embedding:8b'")
        conn.execute("UPDATE rule_embeddings SET model = 'qwen3-embedding:8b'")
        recorded_cards = search_cards(
            conn, real_embedder, "artifact that adds mana", reranker=real_reranker
        )
        recorded_rules = search_rules(conn, real_embedder, "commander color identity")

    replay = recording.model_copy(update={"model_provider": "replay"})
    with connect(loaded) as conn:
        replayed_cards = search_cards(
            conn,
            build_embedder(replay),
            "artifact that adds mana",
            reranker=build_reranker(replay),
        )
        replayed_rules = search_rules(conn, build_embedder(replay), "commander color identity")

    assert replayed_cards == recorded_cards
    assert replayed_rules == recorded_rules
    assert len(recorded_cards) >= 5
