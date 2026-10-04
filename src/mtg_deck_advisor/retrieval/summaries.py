"""One-sentence card summaries, written by a local model, for pool searches (#74, #76).

A summary says what a card does in the words a player would search with:
Game Over becomes "Destroys all creatures ... Used for board wipe". Embedded
as its own vector and fused into pool searches, summaries lifted dev-set
recall@10 for deck-building needs from 0.67 to 0.78 (#74). They aren't used
for catalogue searches: they leave out the card's name on purpose, which made
name searches worse.

A summary is only a retrieval signal. It can be wrong where a card gives the
model nothing to go on (a vanilla creature was once given haste), so nothing
downstream treats it as the card's text: the agent and the deck validator
always read the real rules text.

Summaries are stored against the card's content hash and the prompt version,
like role tags, so only new or changed cards are summarised again: the whole
catalogue took about 6.6 h of local GPU time once, and a typical Scryfall
update adds a few dozen cards.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

import psycopg
import structlog
from pydantic import BaseModel, Field

from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.errors import InvalidOutputError, ModelError
from mtg_deck_advisor.llm.types import Message, ModelRequest

log = structlog.get_logger(__name__)

# Change it whenever the prompt changes, so stored summaries are redone.
SUMMARY_PROMPT_VERSION = "summaries-v1"
DEFAULT_BATCH_SIZE = 25
SYSTEM_PROMPT = (
    "You describe Magic: The Gathering cards for a search index. For each numbered card, "
    "write one plain-English sentence saying what the card does and what a Commander player "
    "would use it for, in the everyday words a player would search with (for example ramp, "
    "draws cards, destroys a creature, makes tokens, counters a spell). Don't repeat the "
    "card's name or copy its rules wording. Write 15 to 30 words."
)


class CardSummary(BaseModel):
    index: int
    summary: str = Field(min_length=10, max_length=300)


class CardSummaries(BaseModel):
    cards: list[CardSummary]


class SummaryReport(BaseModel):
    added: int
    updated: int
    unchanged: int
    # Cards the model couldn't summarise even one at a time; tried again next run.
    failed: int


@dataclass(frozen=True)
class CardToSummarise:
    oracle_id: UUID
    content_hash: str
    name: str
    type_line: str
    mana_cost: str
    oracle_text: str


def build_request(cards: Sequence[CardToSummarise]) -> ModelRequest:
    listing = "\n".join(
        f"{number}. {card.name} | {card.mana_cost} | {card.type_line} | "
        f"{card.oracle_text.replace(chr(10), ' / ')}"
        for number, card in enumerate(cards, start=1)
    )
    return ModelRequest(
        purpose="card_summaries",
        system=SYSTEM_PROMPT,
        messages=(Message(role="user", content=f"Describe these cards:\n{listing}"),),
        max_tokens=max(800, 90 * len(cards)),
        effort="low",
    )


def summarise_batch(client: ModelClient, cards: Sequence[CardToSummarise]) -> list[str]:
    """One summary per card, in order; the answer must cover every card exactly once."""
    result = client.generate_structured(build_request(cards), CardSummaries)
    by_index = {card.index: card.summary for card in result.value.cards}
    if sorted(by_index) != list(range(1, len(cards) + 1)):
        raise InvalidOutputError(
            f"summaries answered for cards {sorted(by_index)}; expected 1 to {len(cards)}"
        )
    return [by_index[number] for number in range(1, len(cards) + 1)]


def _summarise_with_fallback(
    client: ModelClient, batch: Sequence[CardToSummarise]
) -> list[tuple[CardToSummarise, str]]:
    """A batch's summaries, retrying card by card if the batch answer is incomplete.

    At temperature 0 a bad batch fails the same way every time (the model once
    skipped one of two vanilla cards in a batch), so retrying the batch is useless.
    """
    try:
        return list(zip(batch, summarise_batch(client, batch), strict=True))
    except ModelError as exc:
        log.warning("summary_batch_failed", cards=len(batch), error=str(exc))
    results = []
    for card in batch:
        try:
            (summary,) = summarise_batch(client, [card])
        except ModelError as exc:
            log.warning("summary_failed", card=card.name, error=str(exc))
            continue
        results.append((card, summary))
    return results


def summarise_cards(
    conn: psycopg.Connection, client: ModelClient, *, batch_size: int = DEFAULT_BATCH_SIZE
) -> SummaryReport:
    """Summarise every Commander-legal card without a current summary; each batch commits."""
    rows = conn.execute(
        """
        SELECT c.oracle_id, c.content_hash, c.name, c.type_line, c.mana_cost, c.oracle_text,
               s.oracle_id IS NOT NULL
        FROM cards c LEFT JOIN card_summaries s USING (oracle_id)
        WHERE c.removed_at IS NULL AND c.commander_legality = 'legal'
          AND (s.oracle_id IS NULL OR s.content_hash <> c.content_hash
               OR s.prompt_version <> %s)
        ORDER BY c.name
        """,
        (SUMMARY_PROMPT_VERSION,),
    ).fetchall()
    total = conn.execute(
        "SELECT count(*) FROM cards WHERE removed_at IS NULL AND commander_legality = 'legal'"
    ).fetchone()
    conn.commit()  # end the reads, so each batch below commits on its own
    stale = [(CardToSummarise(*row[:6]), bool(row[6])) for row in rows]
    existing = {card.oracle_id for card, had_summary in stale if had_summary}

    added = updated = failed = 0
    for start in range(0, len(stale), batch_size):
        batch = [card for card, _ in stale[start : start + batch_size]]
        results = _summarise_with_fallback(client, batch)
        failed += len(batch) - len(results)
        with conn.transaction():
            conn.cursor().executemany(
                """
                INSERT INTO card_summaries (oracle_id, summary, content_hash, prompt_version, model)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (oracle_id) DO UPDATE SET
                    summary = EXCLUDED.summary,
                    content_hash = EXCLUDED.content_hash,
                    prompt_version = EXCLUDED.prompt_version,
                    model = EXCLUDED.model,
                    summarised_at = now()
                """,
                [
                    (
                        card.oracle_id,
                        summary,
                        card.content_hash,
                        SUMMARY_PROMPT_VERSION,
                        client.model,
                    )
                    for card, summary in results
                ],
            )
        for card, _ in results:
            if card.oracle_id in existing:
                updated += 1
            else:
                added += 1

    report = SummaryReport(
        added=added,
        updated=updated,
        unchanged=(total[0] if total else 0) - len(stale),
        failed=failed,
    )
    log.info("summaries_finished", model=client.model, **report.model_dump())
    return report
