"""Measure candidate local (Ollama) models, to choose them on evidence. See ADR 0009.

    python scripts/measure_local_models.py embeddings
    python scripts/measure_local_models.py chat

Needs the card database loaded and Ollama running with the candidates pulled.
Free to run: local models cost nothing.

These are model-selection checks, small by design. The proper retrieval
evaluation is Milestone 4 (EVL-1), and the proper role-tagging comparison is
issue #51.
"""

import argparse
import math
import re
import statistics
import sys
import time
from typing import Literal

import httpx2
from pydantic import BaseModel

from mtg_deck_advisor.config import get_settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.errors import ModelError
from mtg_deck_advisor.llm.ollama import OllamaEmbedder, OllamaProvider
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.types import Message, ModelRequest
from mtg_deck_advisor.observability.logging import configure_logging

EMBEDDING_CANDIDATES = {
    # model: (query prefix, document prefix), as each model's card documents.
    "nomic-embed-text": ("search_query: ", "search_document: "),
    "mxbai-embed-large": ("Represent this sentence for searching relevant passages: ", ""),
    "qwen3-embedding:0.6b": (
        "Instruct: Given a description of a card's effect, retrieve the Magic: The "
        "Gathering card that has it\nQuery: ",
        "",
    ),
}
CHAT_CANDIDATES = ["qwen3:8b", "gemma3:12b", "qwen3:14b"]

# Plain-English descriptions of real cards' effects, deliberately worded
# differently from the cards' own rules text.
QUERIES = {
    "Sol Ring": "an artifact that taps for two colorless mana",
    "Lightning Bolt": "an instant that deals three damage to a creature or player",
    "Wrath of God": "kill every creature on the battlefield",
    "Counterspell": "stop an opponent's spell from resolving",
    "Demonic Tutor": "find any card from my deck and put it into my hand",
    "Harmonize": "a sorcery that lets me draw three cards",
    "Animate Dead": "bring a dead creature back from a graveyard under my control",
    "Rampant Growth": "fetch one basic land from my deck onto the battlefield",
    "Swords to Plowshares": "remove a creature from the game; its owner gains life",
    "Glorious Anthem": "make all my creatures bigger by one",
    "Raise the Alarm": "make two small soldier tokens at instant speed",
    "Lightning Greaves": "equipment that makes a creature untargetable and fast",
    "Craterhoof Behemoth": "a big creature that pumps my whole team so they trample for lethal",
    "Command Tower": "a land that makes any color my commander can use",
    "Control Magic": "steal an opponent's creature permanently",
    "Cultivate": "fetch two basic lands, one to the battlefield and one to my hand",
    "Eternal Witness": "a creature that returns a card from my graveyard to my hand",
    "Beast Within": "destroy any permanent, but its controller gets a 3/3 token",
    "Rhystic Study": "draw cards whenever opponents cast spells unless they pay one",
    "Smothering Tithe": "get treasure whenever an opponent draws unless they pay two",
}
CORPUS_SIZE = 2000

Role = Literal[
    "ramp",
    "card_draw",
    "removal",
    "board_wipe",
    "counterspell",
    "tutor",
    "recursion",
    "protection",
    "token_maker",
    "finisher",
    "mana_fixing",
    "land",
]


class RoleGuess(BaseModel):
    roles: list[Role]


# Cards whose main role is beyond dispute, for a sanity check of output quality.
OBVIOUS_ROLES: dict[str, Role] = {
    "Sol Ring": "ramp",
    "Swords to Plowshares": "removal",
    "Wrath of God": "board_wipe",
    "Counterspell": "counterspell",
    "Demonic Tutor": "tutor",
    "Harmonize": "card_draw",
    "Animate Dead": "recursion",
    "Raise the Alarm": "token_maker",
    "Lightning Greaves": "protection",
    "Craterhoof Behemoth": "finisher",
    "Rampant Growth": "ramp",
    "Command Tower": "land",
}

SYSTEM = (
    "You classify Magic: The Gathering cards by the roles they play in a Commander deck. "
    "Choose every role that applies from: ramp, card_draw, removal, board_wipe, counterspell, "
    "tutor, recursion, protection, token_maker, finisher, mana_fixing, land."
)


SYMBOL_WORDS = {
    "T": "tap",
    "Q": "untap",
    "C": "one colorless mana",
    "W": "one white mana",
    "U": "one blue mana",
    "B": "one black mana",
    "R": "one red mana",
    "G": "one green mana",
    "S": "one snow mana",
    "X": "X mana",
    "E": "one energy",
}
NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def expand_symbols(text: str) -> str:
    """Rules text with mana and tap symbols written out as words, for embedding."""

    def word(match: re.Match[str]) -> str:
        symbol = match[1]
        if symbol.isdigit():
            number = int(symbol)
            amount = NUMBER_WORDS[number] if number < len(NUMBER_WORDS) else symbol
            return f" {amount} generic mana "
        return f" {SYMBOL_WORDS.get(symbol, symbol)} "

    return re.sub(r"\s+", " ", re.sub(r"\{([^}]+)\}", word, text)).strip()


EXPAND = False


def card_text(name: str, type_line: str, oracle_text: str) -> str:
    text = expand_symbols(oracle_text) if EXPAND else oracle_text
    return f"{name}. {type_line}. {text}"


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def measure_embeddings() -> None:
    settings = get_settings()
    with connect(settings) as conn:
        corpus = conn.execute(
            """
            SELECT name, type_line, oracle_text FROM cards
            WHERE removed_at IS NULL AND commander_legality = 'legal' AND NOT name = ANY(%s)
            ORDER BY md5(oracle_id::text) LIMIT %s
            """,
            (list(QUERIES), CORPUS_SIZE),
        ).fetchall()
        corpus += conn.execute(
            "SELECT name, type_line, oracle_text FROM cards WHERE removed_at IS NULL "
            "AND commander_legality = 'legal' AND name = ANY(%s)",
            (list(QUERIES),),
        ).fetchall()
    names = [row[0] for row in corpus]
    print(f"corpus: {len(corpus)} cards ({len(QUERIES)} targets), {len(QUERIES)} queries\n")
    print(f"{'model':22} {'dims':>5} {'recall@1':>9} {'recall@10':>10} {'MRR':>6} {'cards/s':>8}")

    for model, (query_prefix, doc_prefix) in EMBEDDING_CANDIDATES.items():
        embedder = OllamaEmbedder(
            base_url=settings.ollama_base_url,
            model=model,
            timeout_seconds=settings.ollama_timeout_seconds,
        )
        embedder.embed(["warm-up"])  # load the model before timing
        started = time.perf_counter()
        documents = embedder.embed([doc_prefix + card_text(*row) for row in corpus])
        rate = len(corpus) / (time.perf_counter() - started)
        queries = embedder.embed([query_prefix + text for text in QUERIES.values()])

        ranks = []
        for target, query in zip(QUERIES, queries, strict=True):
            scores = sorted(
                ((cosine(query, doc), name) for doc, name in zip(documents, names, strict=True)),
                reverse=True,
            )
            ranks.append(1 + [name for _, name in scores].index(target))
        recall_1 = sum(rank == 1 for rank in ranks) / len(ranks)
        recall_10 = sum(rank <= 10 for rank in ranks) / len(ranks)
        mrr = statistics.mean(1 / rank for rank in ranks)
        print(
            f"{model:22} {len(documents[0]):>5} {recall_1:>9.2f} {recall_10:>10.2f} "
            f"{mrr:>6.3f} {rate:>8.0f}"
        )
        missed = [f"{t} (rank {r})" for t, r in zip(QUERIES, ranks, strict=True) if r > 10]
        if missed:
            print(f"{'':22} missed top 10: {', '.join(missed)}")


def vram_gb(base_url: str, model: str) -> float:
    running = httpx2.get(f"{base_url}/api/ps", timeout=10).json().get("models", [])
    return next((m["size_vram"] / 1e9 for m in running if m["name"] == model), 0.0)


def unload_all(base_url: str) -> None:
    """Unload every model, so the next one has the GPU to itself.

    Ollama keeps models loaded for a while after use. On Windows, AMD drivers
    can extend GPU memory into much slower shared system RAM, which Ollama
    still reports as VRAM, so a candidate measured while others are loaded
    runs partly from system RAM and looks far slower than it is.
    """
    running = httpx2.get(f"{base_url}/api/ps", timeout=10).json().get("models", [])
    for model in running:
        httpx2.post(
            f"{base_url}/api/generate",
            json={"model": model["name"], "keep_alive": 0},
            timeout=60,
        )


def measure_chat() -> None:
    settings = get_settings()
    with connect(settings) as conn:
        cards = conn.execute(
            "SELECT name, type_line, oracle_text FROM cards WHERE removed_at IS NULL "
            "AND commander_legality = 'legal' AND name = ANY(%s)",
            (list(OBVIOUS_ROLES),),
        ).fetchall()
    print(f"{len(cards)} cards with an obvious main role\n")
    print(
        f"{'model':12} {'first load':>10} {'warm avg':>9} {'tok/s':>6} "
        f"{'valid':>6} {'hits':>5} {'VRAM':>6}"
    )

    for model in CHAT_CANDIDATES:
        unload_all(settings.ollama_base_url)
        provider = OllamaProvider(
            base_url=settings.ollama_base_url, timeout_seconds=settings.ollama_timeout_seconds
        )
        recorder = MemoryRecorder()
        client = ModelClient(provider, model, recorder=recorder)
        latencies, valid, hits, tokens, seconds = [], 0, 0, 0, 0.0
        for name, type_line, oracle_text in cards:
            request = ModelRequest(
                purpose="local_model_measurement",
                system=SYSTEM,
                messages=(Message(role="user", content=card_text(name, type_line, oracle_text)),),
                max_tokens=200,
            )
            try:
                result = client.generate_structured(request, RoleGuess)
            except ModelError as exc:
                print(f"  {model}: {name}: {exc}", file=sys.stderr)
                continue
            valid += result.attempts == 1
            hits += OBVIOUS_ROLES[name] in result.value.roles
            latencies.append(result.response.latency_ms)
            tokens += result.response.usage.output_tokens
            seconds += result.response.latency_ms / 1000
        warm = statistics.mean(latencies[1:]) if len(latencies) > 1 else float("nan")
        print(
            f"{model:12} {latencies[0] / 1000 if latencies else float('nan'):>9.1f}s "
            f"{warm / 1000:>8.2f}s {tokens / seconds if seconds else 0:>6.0f} "
            f"{valid:>3}/{len(cards):<2} {hits:>2}/{len(cards):<2} "
            f"{vram_gb(settings.ollama_base_url, model):>5.1f}G"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("part", choices=["embeddings", "chat"])
    parser.add_argument(
        "--expand-symbols",
        action="store_true",
        help="write mana and tap symbols out as words in card text",
    )
    args = parser.parse_args()
    configure_logging(get_settings().model_copy(update={"log_level": "WARNING"}))
    EXPAND = args.expand_symbols
    part = args.part
    measure_embeddings() if part == "embeddings" else measure_chat()
