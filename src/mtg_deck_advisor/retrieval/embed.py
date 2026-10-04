"""Embed for semantic search: `python -m mtg_deck_advisor.retrieval.embed cards|rules|all`.

A separate step after ingestion (like migrations, ADR 0003): ingestion never
needs a model, and embedding can be rerun on its own. It uses the local
EMBEDDING_MODEL through Ollama, so it costs nothing. Safe to run repeatedly:
only missing or stale embeddings are made.
"""

import argparse
import sys
import time
from collections.abc import Sequence

from mtg_deck_advisor.config import get_settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.llm.errors import ModelError
from mtg_deck_advisor.llm.factory import build_embedder
from mtg_deck_advisor.observability.logging import configure_logging
from mtg_deck_advisor.observability.tracing import traced
from mtg_deck_advisor.retrieval.embeddings import EmbeddingReport, embed_cards, embed_rules

COMMANDS = ("cards", "rules", "all")
JOBS = {"cards": embed_cards, "rules": embed_rules}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m mtg_deck_advisor.retrieval.embed")
    parser.add_argument("what", choices=COMMANDS, help="what to embed")
    return parser.parse_args(argv)


def summary(name: str, report: EmbeddingReport, seconds: float) -> str:
    return (
        f"{name}: {report.added:,} added, {report.updated:,} updated, "
        f"{report.unchanged:,} unchanged; {seconds:.1f} s"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings)
    embedder = build_embedder(settings)
    names = list(JOBS) if args.what == "all" else [args.what]
    with traced(), connect(settings) as conn:
        for name in names:
            started = time.perf_counter()
            try:
                report = JOBS[name](conn, embedder)
            except ModelError as exc:
                print(f"embedding stopped: {exc}", file=sys.stderr)
                return 1
            print(summary(name, report, time.perf_counter() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
