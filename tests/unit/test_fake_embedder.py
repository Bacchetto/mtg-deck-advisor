"""The fake embedder that lets retrieval run in tests and CI with no model."""

import math

from mtg_deck_advisor.llm.fake import FakeEmbedder


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_vectors_are_deterministic_normalised_and_full_size() -> None:
    first, again = FakeEmbedder().embed(["Sol Ring"]), FakeEmbedder().embed(["Sol Ring"])

    assert first == again
    assert len(first[0]) == 1024
    assert math.isclose(sum(x * x for x in first[0]), 1.0)


def test_texts_sharing_words_are_closer_than_unrelated_texts() -> None:
    ramp, more_ramp, unrelated = FakeEmbedder().embed(
        ["add one green mana", "Add one green mana.", "destroy target creature"]
    )

    assert cosine(ramp, more_ramp) > 0.99
    assert cosine(ramp, more_ramp) > cosine(ramp, unrelated)


def test_an_empty_text_still_gets_a_unit_vector() -> None:
    (vector,) = FakeEmbedder().embed([""])

    assert math.isclose(sum(x * x for x in vector), 1.0)


def test_it_records_what_it_embedded_and_names_its_model() -> None:
    embedder = FakeEmbedder(model="fake-2")
    embedder.embed(["a", "b"])

    assert embedder.model == "fake-2"
    assert embedder.calls == [["a", "b"]]
