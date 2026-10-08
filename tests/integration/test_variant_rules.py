"""Rules search leaves out other variants' rules unless the question names them (#138).

In the rules Q&A baseline, 10 of Sonnet's 11 failures cited Brawl, Commander
Draft or team-variant rules beside Commander's, because search returned them:
they share Commander's words. One answer (R11) said a planeswalker can be a
commander, which is Brawl's rule.
"""

from pathlib import Path

import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.ingestion.rules import parse_rules
from mtg_deck_advisor.ingestion.rules_ingestion import apply_rules
from mtg_deck_advisor.llm.fake import FakeEmbedder
from mtg_deck_advisor.retrieval.embeddings import embed_rules
from mtg_deck_advisor.retrieval.search import SearchMode, search_rules

EXCERPT = Path(__file__).parent.parent / "fixtures" / "rules" / "comprehensive_rules_excerpt.txt"
# Written to share a Commander question's words, as the real ones do.
VARIANT_RULES = (
    "903.12c In Brawl, a planeswalker card can be your commander, a commander "
    "chosen from planeswalker cards.\n"
    "903.13f In Commander Draft, a commander can be a planeswalker card, and a "
    "commander deck has 40 cards.\n"
)


@pytest.fixture
def rules(settings: Settings) -> Settings:
    upgrade(settings)
    text = EXCERPT.read_text(encoding="utf-8")
    anchor = "903.4c "
    at = text.index(anchor)
    line_end = text.index("\n", at) + 1
    with connect(settings) as conn:
        apply_rules(conn, parse_rules(text[:line_end] + VARIANT_RULES + text[line_end:]).rules)
        embed_rules(conn, FakeEmbedder())
    return settings


def numbers(settings: Settings, question: str, mode: SearchMode = "vector") -> list[str]:
    with connect(settings) as conn:
        return [hit.number for hit in search_rules(conn, FakeEmbedder(), question, mode=mode)]


@pytest.mark.parametrize("mode", ["vector", "keyword", "hybrid"])
def test_a_commander_question_gets_no_other_variants_rules(
    rules: Settings, mode: SearchMode
) -> None:
    found = numbers(rules, "Can a planeswalker be my commander?", mode)

    assert "903.12c" not in found and "903.13f" not in found


def test_a_question_naming_a_variant_gets_its_rules(rules: Settings) -> None:
    found = numbers(rules, "Can a planeswalker be my commander in Brawl?")

    assert "903.12c" in found
    assert "903.13f" not in found


def test_every_variant_can_still_be_searched_on_request(rules: Settings) -> None:
    with connect(rules) as conn:
        hits = search_rules(
            conn, FakeEmbedder(), "Can a planeswalker be my commander?", all_variants=True
        )

    assert {"903.12c", "903.13f"} <= {hit.number for hit in hits}
