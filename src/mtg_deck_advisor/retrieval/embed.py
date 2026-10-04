"""Embed for search: `python -m mtg_deck_advisor.retrieval.embed cards|rules|summaries|all`.

A separate step after ingestion (like migrations, ADR 0003): ingestion never
needs a model, and embedding can be rerun on its own. It uses local models
through Ollama, so it costs nothing. Safe to run repeatedly: only missing or
stale embeddings are made.

`summaries` first has SUMMARY_MODEL write a one-sentence summary for every
card without a current one (about 0.75 s a card), then embeds the summaries.
"""

import argparse
import sys
import time
from collections.abc import Callable, Sequence

import psycopg

from mtg_deck_advisor.config import Settings, get_settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.errors import ModelError
from mtg_deck_advisor.llm.factory import build_embedder
from mtg_deck_advisor.llm.ollama import Embedder, OllamaProvider
from mtg_deck_advisor.llm.recording import DatabaseRecorder
from mtg_deck_advisor.observability.logging import configure_logging
from mtg_deck_advisor.observability.tracing import traced
from mtg_deck_advisor.retrieval.embeddings import (
    EmbeddingReport,
    embed_cards,
    embed_rules,
    embed_summaries,
)
from mtg_deck_advisor.retrieval.summaries import SummaryReport, summarise_cards

COMMANDS = ("cards", "rules", "summaries", "all")
JOBS: dict[str, Callable[[psycopg.Connection, Embedder], EmbeddingReport]] = {
    "cards": embed_cards,
    "rules": embed_rules,
    "summaries": embed_summaries,
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m mtg_deck_advisor.retrieval.embed")
    parser.add_argument("what", choices=COMMANDS, help="what to embed")
    return parser.parse_args(argv)


def summary(name: str, report: EmbeddingReport, seconds: float) -> str:
    return (
        f"{name}: {report.added:,} added, {report.updated:,} updated, "
        f"{report.unchanged:,} unchanged; {seconds:.1f} s"
    )


def summaries_summary(report: SummaryReport, seconds: float) -> str:
    return (
        f"summaries written: {report.added:,} added, {report.updated:,} updated, "
        f"{report.unchanged:,} unchanged, {report.failed:,} failed; {seconds:.1f} s"
    )


def summary_client(settings: Settings) -> ModelClient:
    """The local model that writes card summaries, with every call recorded."""
    provider = OllamaProvider(
        base_url=settings.ollama_base_url, timeout_seconds=settings.ollama_timeout_seconds
    )
    return ModelClient(provider, settings.summary_model, recorder=DatabaseRecorder(settings))


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
                if name == "summaries":
                    written = summarise_cards(conn, summary_client(settings))
                    print(summaries_summary(written, time.perf_counter() - started))
                    started = time.perf_counter()
                report = JOBS[name](conn, embedder)
            except ModelError as exc:
                print(f"embedding stopped: {exc}", file=sys.stderr)
                return 1
            print(summary(name, report, time.perf_counter() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
