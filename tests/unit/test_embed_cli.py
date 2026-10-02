"""The embedding command: `python -m mtg_deck_advisor.retrieval.embed cards`."""

import pytest

from mtg_deck_advisor.retrieval import embed as cli
from mtg_deck_advisor.retrieval.embeddings import EmbeddingReport


@pytest.mark.parametrize("what", ["cards", "rules", "all"])
def test_it_takes_what_to_embed(what: str) -> None:
    assert cli.parse_args([what]).what == what


def test_what_to_embed_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.parse_args([])

    assert "cards" in capsys.readouterr().err


def test_the_summary_gives_counts_and_time() -> None:
    report = EmbeddingReport(added=31950, updated=3, unchanged=12)

    assert cli.summary("cards", report, seconds=312.44) == (
        "cards: 31,950 added, 3 updated, 12 unchanged; 312.4 s"
    )
