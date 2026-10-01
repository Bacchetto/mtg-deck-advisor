"""Rules ingestion against a real database, from an excerpt of the real rules file."""

from pathlib import Path

import httpx2
import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.ingestion.rules import RuleRecord, parse_rules
from mtg_deck_advisor.ingestion.rules_ingestion import apply_rules, ingest_rules

EXCERPT = Path(__file__).parent.parent / "fixtures" / "rules" / "comprehensive_rules_excerpt.txt"


@pytest.fixture
def migrated(settings: Settings) -> Settings:
    upgrade(settings)
    return settings


def excerpt_rules() -> list[RuleRecord]:
    return parse_rules(EXCERPT.read_text(encoding="utf-8")).rules


def apply(settings: Settings, rules: list[RuleRecord]) -> tuple[int, int, int, int]:
    with connect(settings) as conn:
        report = apply_rules(conn, rules)
    return report.added, report.updated, report.unchanged, report.removed


def test_rules_are_stored_and_a_second_run_changes_nothing(migrated: Settings) -> None:
    assert apply(migrated, excerpt_rules()) == (10, 0, 0, 0)
    assert apply(migrated, excerpt_rules()) == (0, 0, 10, 0)

    with connect(migrated) as conn:
        row = conn.execute(
            "SELECT parent, section, left(text, 40) FROM rules WHERE number = '903.4a'"
        ).fetchone()
    assert row == ("903.4", "903. Commander", "Color identity is established before the")


def test_a_reworded_rule_is_updated_and_a_dropped_rule_is_removed(migrated: Settings) -> None:
    apply(migrated, excerpt_rules())
    rules = excerpt_rules()
    rules[0] = rules[0].model_copy(update={"text": "Reworded."})
    rules = [rule for rule in rules if rule.number != "704.5aa"]

    assert apply(migrated, rules) == (0, 1, 8, 1)


def test_ingest_rules_downloads_parses_and_records_the_run(
    migrated: Settings, tmp_path: Path
) -> None:
    body = EXCERPT.read_bytes()
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, stream=httpx2.ByteStream(body))

    settings = migrated.model_copy(
        update={
            "cache_dir": tmp_path,
            "rules_url": "https://media.example.test/MagicCompRules%2020260925.txt",
        }
    )
    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
        first = ingest_rules(settings, client)
        second = ingest_rules(settings, client)

    assert (first.added, second.unchanged) == (10, 10)
    assert len(requests) == 1  # the second run used the cached file

    with connect(migrated) as conn:
        runs = conn.execute(
            "SELECT source, source_version, added, unchanged FROM ingestion_runs ORDER BY id"
        ).fetchall()
    assert runs == [
        ("comprehensive_rules", "2026-09-25", 10, 0),
        ("comprehensive_rules", "2026-09-25", 0, 10),
    ]
