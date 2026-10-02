"""Compare role-tagging models against the human-reviewed gold sample (MOD-5).

    python scripts/compare_role_taggers.py --estimate   # free: projected cost only
    python scripts/compare_role_taggers.py --run        # tags the gold sample with each model

Cards are tagged in batches of 25, as production will tag them, so the scores
predict production behaviour. Every response is recorded under
recordings/role_tagging/, so a run can be re-scored later for free
(MODEL_PROVIDER=replay). Each model's spend is capped, and every call is
logged to model_calls. The report is saved, dated, under evals/reports/.
"""

import argparse
import csv
import statistics
import sys
import time
from datetime import date
from pathlib import Path
from typing import get_args
from uuid import UUID

from mtg_deck_advisor.config import get_settings
from mtg_deck_advisor.evaluation.classification import ClassificationScore, score
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.errors import ModelError
from mtg_deck_advisor.llm.factory import build_provider
from mtg_deck_advisor.llm.pricing import cost_usd
from mtg_deck_advisor.llm.recording import DatabaseRecorder
from mtg_deck_advisor.llm.roles import SYSTEM_PROMPT, CardToTag, Role, build_request, tag_cards
from mtg_deck_advisor.llm.types import Usage
from mtg_deck_advisor.observability.logging import configure_logging
from mtg_deck_advisor.observability.tracing import traced

GOLD = Path("evals/datasets/role_tagging_gold.csv")
RECORDINGS = Path("recordings/role_tagging")
REPORTS = Path("evals/reports")
BATCH_SIZE = 25
COST_CAP_PER_MODEL_USD = 1.00
CANDIDATES: list[tuple[str, str]] = [
    ("anthropic", "claude-opus-5-5"),
    ("anthropic", "claude-sonnet-5-5"),
    ("anthropic", "claude-haiku-4-5"),
    ("ollama", "qwen3:14b"),
]
ROLES: list[str] = list(get_args(Role))
# For the estimate: about 4 characters per token, and per card about 30 output
# tokens of JSON plus a one-sentence reason. Reasoning tokens on models that
# think are unknown in advance, so the estimate doubles the output for them.
CHARS_PER_TOKEN = 4
OUTPUT_TOKENS_PER_CARD = 55
THINKING_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


def load_gold() -> tuple[list[CardToTag], list[set[str]]]:
    with GOLD.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    # Spreadsheet editors may save line breaks inside rules text as CRLF.
    rows = [{k: v.replace("\r\n", "\n") for k, v in row.items()} for row in rows]
    cards = [
        CardToTag(
            oracle_id=UUID(row["oracle_id"]),
            name=row["name"],
            type_line=row["type_line"],
            mana_cost=row["mana_cost"],
            oracle_text=row["oracle_text"],
        )
        for row in rows
    ]
    unknown = {r for row in rows for r in row["roles"].split()} - set(ROLES)
    if unknown:
        sys.exit(f"gold file has roles outside the taxonomy: {sorted(unknown)}")
    return cards, [set(row["roles"].split()) for row in rows]


def batches(cards: list[CardToTag]) -> list[list[CardToTag]]:
    return [cards[i : i + BATCH_SIZE] for i in range(0, len(cards), BATCH_SIZE)]


def estimate(cards: list[CardToTag]) -> None:
    print(f"{len(cards)} cards in {len(batches(cards))} batches of up to {BATCH_SIZE}\n")
    total = 0.0
    for provider, model in CANDIDATES:
        if provider != "anthropic":
            print(f"  {model:20} free (local)")
            continue
        input_tokens = sum(
            (len(SYSTEM_PROMPT) + len(build_request(batch).messages[0].content)) // CHARS_PER_TOKEN
            for batch in batches(cards)
        )
        output_tokens = OUTPUT_TOKENS_PER_CARD * len(cards)
        if model in THINKING_MODELS:
            output_tokens *= 2
        cost = cost_usd(model, Usage(input_tokens=input_tokens, output_tokens=output_tokens))
        total += cost
        print(f"  {model:20} ~${cost:.3f}  ({input_tokens} in, ~{output_tokens} out)")
    print(
        f"\n  estimated total: ~${total:.2f} (each model capped at ${COST_CAP_PER_MODEL_USD:.2f})"
    )


def fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def run(cards: list[CardToTag], gold: list[set[str]]) -> None:
    settings = get_settings()
    lines = [
        f"# Role tagging comparison, {date.today().isoformat()}",
        "",
        f"Gold sample: `{GOLD}`, {len(cards)} cards with owner-reviewed labels. "
        f"Batches of {BATCH_SIZE}, as in production. Taxonomy and prompt: `llm/roles.py`.",
        "",
        "| Model | micro P | micro R | micro F1 | exact match | invalid batches | "
        "cost | per 1,000 cards | s/batch |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    per_role: dict[str, ClassificationScore] = {}
    for provider_name, model in CANDIDATES:
        run_settings = settings.model_copy(
            update={
                "model_provider": provider_name,
                "model_name": model,
                "record_responses": True,
                "replay_dir": RECORDINGS,
            }
        )
        client = ModelClient(
            build_provider(run_settings),
            model,
            recorder=DatabaseRecorder(settings),
            cost_cap_usd=COST_CAP_PER_MODEL_USD,
        )
        predicted: dict[UUID, set[str]] = {}
        invalid, seconds = 0, []
        for batch in batches(cards):
            started = time.perf_counter()
            try:
                with traced():
                    tagged = tag_cards(client, batch)
            except ModelError as exc:
                invalid += 1
                print(f"  {model}: batch failed: {exc}", file=sys.stderr)
                continue
            seconds.append(time.perf_counter() - started)
            predicted |= {oracle_id: set(t.roles) for oracle_id, t in tagged.items()}
        # A card from a failed batch counts as predicting no roles.
        result = score(gold, [predicted.get(card.oracle_id, set()) for card in cards], ROLES)
        per_role[model] = result
        per_thousand = client.spent_usd / len(cards) * 1000
        lines.append(
            f"| {model} | {fmt(result.micro.precision)} | {fmt(result.micro.recall)} | "
            f"{fmt(result.micro.f1)} | {result.exact_match:.2f} | {invalid} | "
            f"${client.spent_usd:.3f} | ${per_thousand:.2f} | "
            f"{statistics.mean(seconds) if seconds else float('nan'):.1f} |"
        )
        print(lines[-1])

    lines += ["", "## F1 by role", "", "| Role | support | " + " | ".join(per_role) + " |"]
    lines.append("|---|---|" + "---|" * len(per_role))
    for role in ROLES:
        support = next(iter(per_role.values())).per_label[role].support
        lines.append(
            f"| {role} | {support} | "
            + " | ".join(fmt(result.per_label[role].f1) for result in per_role.values())
            + " |"
        )
    REPORTS.mkdir(parents=True, exist_ok=True)
    report = REPORTS / f"{date.today().isoformat()}-role-tagging-comparison.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nreport: {report}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--estimate", action="store_true", help="projected cost only (free)")
    mode.add_argument("--run", action="store_true", help="tag the gold sample with each model")
    args = parser.parse_args()
    configure_logging(get_settings().model_copy(update={"log_level": "WARNING"}))
    cards, gold = load_gold()
    estimate(cards) if args.estimate else run(cards, gold)
