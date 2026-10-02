"""Tag a pool's cards from the command line: `python -m mtg_deck_advisor.llm.tag_roles --pool FILE`.

Parses a pasted list or CSV export, resolves the names to cards, and tags any
card without fresh stored tags, using ROLE_TAGGING_MODEL through the configured
provider. With MODEL_PROVIDER=replay, recorded answers are replayed for free;
that's how the demo pool is tagged without a key. Spending is capped by
RUN_COST_CAP_USD, and every call is logged to model_calls.
"""

import argparse
import sys
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from mtg_deck_advisor.config import get_settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.ingestion.pool import parse_csv, parse_text
from mtg_deck_advisor.ingestion.resolve import resolve
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.errors import ModelError
from mtg_deck_advisor.llm.factory import build_provider
from mtg_deck_advisor.llm.recording import DatabaseRecorder
from mtg_deck_advisor.llm.tagging import TaggingResult, roles_for
from mtg_deck_advisor.observability.logging import configure_logging
from mtg_deck_advisor.observability.tracing import traced


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m mtg_deck_advisor.llm.tag_roles")
    parser.add_argument("--pool", required=True, type=Path, help="a text list or CSV export")
    parser.add_argument(
        "--format", choices=["text", "csv"], help="default: csv for .csv files, otherwise text"
    )
    args = parser.parse_args(argv)
    if args.format is None:
        args.format = "csv" if args.pool.suffix.lower() == ".csv" else "text"
    return args


def summary(result: TaggingResult, seconds: float) -> str:
    counts = Counter(role for roles in result.roles.values() for role in roles.roles)
    no_role = sum(1 for roles in result.roles.values() if not roles.roles)
    totals = ", ".join(f"{role} {n}" for role, n in counts.most_common())
    return (
        f"{len(result.roles)} cards: {result.tagged} tagged, "
        f"{result.cached} cached, {len(result.failed)} failed; "
        f"cost ${result.cost_usd:.4f}; {seconds:.1f} s\n"
        f"roles: {totals}{', ' if totals else ''}no role {no_role}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings)

    text = args.pool.read_text(encoding="utf-8-sig")
    parsed = parse_csv(text) if args.format == "csv" else parse_text(text)
    for problem in parsed.problems:
        print(f"line {problem.line}: {problem.message}", file=sys.stderr)

    model = settings.role_tagging_model
    client = ModelClient(
        build_provider(settings.model_copy(update={"model_name": model})),
        model,
        recorder=DatabaseRecorder(settings),
        cost_cap_usd=settings.run_cost_cap_usd,
    )
    started = time.perf_counter()
    with traced(), connect(settings) as conn:
        pool = resolve(conn, parsed.entries)
        if pool.unknown or pool.ambiguous:
            print(
                f"{len(pool.unknown)} names not found, {len(pool.ambiguous)} ambiguous "
                "(not tagged)",
                file=sys.stderr,
            )
        try:
            result = roles_for(conn, client, [match.card.oracle_id for match in pool.matched])
        except ModelError as exc:
            print(f"tagging stopped: {exc}", file=sys.stderr)
            return 1
    print(summary(result, time.perf_counter() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
