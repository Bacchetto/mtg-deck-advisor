from pathlib import Path
from uuid import uuid4

import pytest

from mtg_deck_advisor.llm import tag_roles as cli
from mtg_deck_advisor.llm.roles import CardRoles
from mtg_deck_advisor.llm.tagging import TaggingResult


def card_roles(*roles: str) -> CardRoles:
    return CardRoles(
        oracle_id=uuid4(),
        roles=list(roles),
        reason="",
        content_hash="h",
        prompt_version="roles-v2",
        model="claude-opus-5-5",
    )


def test_the_pool_file_format_follows_its_extension(tmp_path: Path) -> None:
    assert cli.parse_args(["--pool", str(tmp_path / "pool.csv")]).format == "csv"
    assert cli.parse_args(["--pool", str(tmp_path / "pool.txt")]).format == "text"
    assert cli.parse_args(["--pool", "pool.txt", "--format", "csv"]).format == "csv"


def test_a_pool_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.parse_args([])

    assert "--pool" in capsys.readouterr().err


def test_the_summary_gives_counts_cost_time_and_role_totals() -> None:
    roles = [card_roles("ramp"), card_roles("ramp", "mana_fixing"), card_roles()]
    result = TaggingResult(
        roles={r.oracle_id: r for r in roles},
        tagged=2,
        cached=1,
        failed=[uuid4()],
        unknown=[],
        cost_usd=0.0123,
    )

    text = cli.summary(result, seconds=4.2)

    assert "3 cards: 2 tagged, 1 cached, 1 failed" in text
    assert "$0.0123" in text
    assert "4.2 s" in text
    assert "ramp 2" in text
    assert "mana_fixing 1" in text
    assert "no role 1" in text
