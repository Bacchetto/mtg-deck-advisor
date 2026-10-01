"""Run ingestion: `python -m mtg_deck_advisor.ingestion cards|rules|all`.

Prints one summary line per source, and logs the same counts with the run's
trace ID. Safe to run repeatedly: a run with nothing new changes nothing.
"""

import argparse
import sys
from collections.abc import Sequence

import httpx2

from mtg_deck_advisor.config import get_settings
from mtg_deck_advisor.ingestion.card_ingestion import ingest_cards
from mtg_deck_advisor.ingestion.rules_ingestion import ingest_rules
from mtg_deck_advisor.ingestion.scryfall import ScryfallClient
from mtg_deck_advisor.ingestion.sync import IngestionReport
from mtg_deck_advisor.observability.logging import configure_logging

COMMANDS = ("cards", "rules", "all")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m mtg_deck_advisor.ingestion")
    parser.add_argument("command", choices=COMMANDS, help="what to ingest")
    return parser.parse_args(argv)


def summary(name: str, report: IngestionReport) -> str:
    return (
        f"{name}: {report.added} added, {report.updated} updated, "
        f"{report.unchanged} unchanged, {report.removed} removed"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings)

    if args.command in ("cards", "all"):
        with ScryfallClient(
            user_agent=settings.scryfall_user_agent, cache_dir=settings.cache_dir
        ) as client:
            report = ingest_cards(settings, client)
        print(summary("cards", report))
    if args.command in ("rules", "all"):
        with httpx2.Client(
            headers={"User-Agent": settings.scryfall_user_agent},
            timeout=30.0,
            follow_redirects=True,
        ) as client:
            report = ingest_rules(settings, client)
        print(summary("rules", report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
