"""The eval gate's pieces that need no database: vector encoding and threshold checks (#119)."""

from pathlib import Path

import pytest

from mtg_deck_advisor.evaluation.gate import check, load_thresholds
from mtg_deck_advisor.evaluation.snapshot import (
    SnapshotEmbedder,
    UnknownQueryError,
    decode_vector,
    encode_vector,
)


def test_a_vector_survives_half_precision_encoding_closely() -> None:
    vector = [0.0, 1.0, -0.5, 0.123456, -0.987654, 1e-3]

    decoded = decode_vector(encode_vector(vector))

    assert len(decoded) == len(vector)
    assert all(abs(a - b) < 1e-3 for a, b in zip(vector, decoded, strict=True))


def test_the_snapshot_embedder_returns_stored_vectors_for_known_texts() -> None:
    embedder = SnapshotEmbedder("qwen3-embedding:8b", {"a": [1.0, 0.0], "b": [0.0, 1.0]})

    assert embedder.model == "qwen3-embedding:8b"
    assert embedder.embed(["b", "a"]) == [[0.0, 1.0], [1.0, 0.0]]


def test_the_snapshot_embedder_refuses_a_text_it_has_no_vector_for() -> None:
    embedder = SnapshotEmbedder("qwen3-embedding:8b", {"a": [1.0, 0.0]})

    with pytest.raises(UnknownQueryError, match="export the snapshot again"):
        embedder.embed(["a new query"])


def test_thresholds_are_read_from_toml(tmp_path: Path) -> None:
    path = tmp_path / "thresholds.toml"
    path.write_text(
        '[dev.cards]\n"recall@10" = 0.8\n"mrr@10" = 0.7\n\n[validator]\naccuracy = 1.0\n',
        encoding="utf-8",
    )

    assert load_thresholds(path) == {
        "dev.cards.recall@10": 0.8,
        "dev.cards.mrr@10": 0.7,
        "validator.accuracy": 1.0,
    }


def test_the_gate_passes_at_or_above_every_threshold() -> None:
    thresholds = {"dev.cards.recall@10": 0.8, "validator.accuracy": 1.0}

    assert check({"dev.cards.recall@10": 0.8, "validator.accuracy": 1.0}, thresholds) == []


def test_the_gate_names_every_metric_below_its_threshold_or_missing() -> None:
    thresholds = {"dev.cards.recall@10": 0.8, "dev.rules.mrr@10": 0.7, "validator.accuracy": 1.0}

    failures = check({"dev.cards.recall@10": 0.79, "validator.accuracy": 1.0}, thresholds)

    assert failures == [
        "dev.cards.recall@10 is 0.790, below its threshold of 0.800",
        "dev.rules.mrr@10 was not measured",
    ]
