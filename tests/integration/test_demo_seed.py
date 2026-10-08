"""The demo dataset and its seed, against real Postgres (#139, DEM-1).

The demo runs without Ollama or an API key, so a newcomer's database is
seeded from a committed dataset: the demo collection's cards with everything
search and the agent read for them (embeddings, summaries, role tags), and
every rule. These tests use fixture cards and the hashing FakeEmbedder.
"""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

from pathlib import Path
from uuid import UUID

import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.deck.store import list_pools
from mtg_deck_advisor.demo.seed import POOL_NAME, AlreadyHasCardsError, collection_cards, seed
from mtg_deck_advisor.evaluation.snapshot import _read_jsonl, export_snapshot
from mtg_deck_advisor.llm.fake import FakeEmbedder
from mtg_deck_advisor.llm.roles import load_roles
from mtg_deck_advisor.retrieval.search import search_cards
from tests.integration.test_agent_tools import RATS, card_id, loaded  # noqa: F401
from tests.integration.test_eval_gate import second_url  # noqa: F401


def summarised(settings: Settings) -> None:
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO card_summaries (oracle_id, summary, content_hash, prompt_version, model) "
            "VALUES (%s, 'Colorless mana rock.', 'h', 'v1', 'fake')",
            (card_id("Sol Ring"),),
        )


def collection(directory: Path) -> Path:
    directory.mkdir()
    (directory / "list.txt").write_text(f"1 Sol Ring\n3 {RATS}\n", encoding="utf-8")
    (directory / "export.csv").write_text(
        "Product Name,Total Quantity,Add to Quantity\n"
        "Lorien Brooch - Sol Ring (Borderless),,1\n"
        f"{RATS},,2\n",
        encoding="utf-8",
    )
    return directory


def dataset(settings: Settings, directory: Path) -> Path:
    with connect(settings) as conn:
        card_ids = [row[0] for row in conn.execute("SELECT oracle_id FROM cards")]
        export_snapshot(
            conn,
            FakeEmbedder(),
            directory,
            card_ids=card_ids,
            queries=[],
            with_roles_and_summaries=True,
        )
    return directory


def test_the_eval_snapshot_still_leaves_out_roles_and_summaries(
    loaded: Settings, tmp_path: Path
) -> None:
    summarised(loaded)
    with connect(loaded) as conn:
        export_snapshot(conn, FakeEmbedder(), tmp_path, card_ids=[card_id("Sol Ring")], queries=[])

    [card] = _read_jsonl(tmp_path / "cards.jsonl.gz")

    assert "_roles" not in card and "_summary" not in card


def test_the_demo_dataset_carries_roles_and_summaries(
    loaded: Settings, second_url: str, tmp_path: Path
) -> None:
    summarised(loaded)
    dataset(loaded, tmp_path / "data")
    copy = Settings(_env_file=None, database_url=second_url)
    upgrade(copy)

    with connect(copy) as conn:
        seed(conn, tmp_path / "data", collection(tmp_path / "collection"))
        roles = load_roles(conn, [card_id("Sol Ring"), card_id(RATS)])
        summary = conn.execute("SELECT summary FROM card_summaries").fetchall()

    assert roles[card_id("Sol Ring")].roles == ["ramp"]
    assert roles[card_id(RATS)].roles == ["finisher"]
    assert summary == [("Colorless mana rock.",)]


def test_a_seeded_database_searches_like_its_source(
    loaded: Settings, second_url: str, tmp_path: Path
) -> None:
    dataset(loaded, tmp_path / "data")
    copy = Settings(_env_file=None, database_url=second_url)
    upgrade(copy)

    with connect(copy) as conn:
        seed(conn, tmp_path / "data", collection(tmp_path / "collection"))
        seeded = search_cards(conn, FakeEmbedder(), "colorless mana rock", mode="vector")
    with connect(loaded) as conn:
        source = search_cards(conn, FakeEmbedder(), "colorless mana rock", mode="vector")

    # To half precision, as the eval snapshot: only near-ties can swap places.
    # The demo is recorded against a seeded database, so replay matches exactly.
    assert seeded[0].name == source[0].name == "Sol Ring"
    assert len(seeded) == len(source)
    for a, b in zip(seeded, source, strict=True):
        assert abs(a.score - b.score) < 0.002


def test_the_seed_creates_the_demo_collection_from_every_file(
    loaded: Settings, second_url: str, tmp_path: Path
) -> None:
    dataset(loaded, tmp_path / "data")
    copy = Settings(_env_file=None, database_url=second_url)
    upgrade(copy)

    with connect(copy) as conn:
        result = seed(conn, tmp_path / "data", collection(tmp_path / "collection"))
        [pool] = list_pools(conn)
        counts: dict[UUID, int] = dict(
            conn.execute(
                "SELECT oracle_id, count FROM pool_cards WHERE pool_id = %s", (pool.id,)
            ).fetchall()
        )

    assert result.seeded and result.unresolved == []
    assert pool.name == POOL_NAME
    # Copies add up across the files; the crossover name finds Sol Ring.
    assert counts == {card_id("Sol Ring"): 2, card_id(RATS): 5}


def test_seeding_again_changes_nothing(loaded: Settings, second_url: str, tmp_path: Path) -> None:
    dataset(loaded, tmp_path / "data")
    copy = Settings(_env_file=None, database_url=second_url)
    upgrade(copy)
    files = collection(tmp_path / "collection")

    with connect(copy) as conn:
        seed(conn, tmp_path / "data", files)
        again = seed(conn, tmp_path / "data", files)
        pools = list_pools(conn)

    assert not again.seeded
    assert len(pools) == 1


def test_the_seed_refuses_a_database_that_already_has_cards(
    loaded: Settings, tmp_path: Path
) -> None:
    dataset(loaded, tmp_path / "data")

    with connect(loaded) as conn, pytest.raises(AlreadyHasCardsError, match="already has cards"):
        seed(conn, tmp_path / "data", collection(tmp_path / "collection"))


def test_the_collection_reads_text_lists_and_csv_exports(loaded: Settings, tmp_path: Path) -> None:
    files = collection(tmp_path / "collection")
    (files / "list.txt").write_text("1 Sol Ring\n1 Not A Card\n", encoding="utf-8")

    with connect(loaded) as conn:
        cards, unresolved = collection_cards(conn, files)

    assert cards == {card_id("Sol Ring"): 2, card_id(RATS): 2}
    assert unresolved == ["Not A Card"]
