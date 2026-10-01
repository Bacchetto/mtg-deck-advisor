import pytest

from mtg_deck_advisor.ingestion import __main__ as cli
from mtg_deck_advisor.ingestion.sync import IngestionReport


def test_the_summary_line_gives_every_count() -> None:
    report = IngestionReport(added=3, updated=2, unchanged=34000, removed=1)

    assert cli.summary("cards", report) == ("cards: 3 added, 2 updated, 34000 unchanged, 1 removed")


def test_an_unknown_command_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.parse_args(["everything"])

    assert excinfo.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_cards_is_a_command() -> None:
    assert cli.parse_args(["cards"]).command == "cards"
