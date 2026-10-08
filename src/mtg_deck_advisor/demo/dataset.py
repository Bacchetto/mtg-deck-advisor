"""Export the demo dataset from the local, fully ingested database (#139, DEM-1).

It holds the demo collection's cards with their embeddings, summaries and
role tags, and every rule with its embedding: the snapshot format, which
`demo.seed` loads. Every name in the collection must resolve, and every card
must already have its embeddings, summary and role tags.

    python -m mtg_deck_advisor.demo.dataset
"""

import json
import sys

from mtg_deck_advisor.demo.seed import COLLECTION_DIR, DATA_DIR, collection_cards


def main() -> int:
    from mtg_deck_advisor.config import get_settings
    from mtg_deck_advisor.db.connection import connect
    from mtg_deck_advisor.evaluation.snapshot import export_snapshot
    from mtg_deck_advisor.llm.factory import build_embedder

    settings = get_settings()
    with connect(settings) as conn:
        cards, unresolved = collection_cards(conn, COLLECTION_DIR)
        if unresolved:
            print(f"names that match no single card: {unresolved}", file=sys.stderr)
            return 1
        manifest = export_snapshot(
            conn,
            build_embedder(settings),
            DATA_DIR,
            card_ids=cards,
            queries=[],
            with_roles_and_summaries=True,
            selection={
                "collection": sorted(p.as_posix() for p in COLLECTION_DIR.iterdir()),
                "copies": sum(cards.values()),
            },
        )
    counts = manifest["counts"]
    missing = {
        name: counts["cards"] - counts[name]
        for name in ("card_embeddings", "summary_embeddings", "card_roles", "card_summaries")
        if counts[name] < counts["cards"]
    }
    print(json.dumps(manifest, indent=2))
    if missing:
        print(f"cards without: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
