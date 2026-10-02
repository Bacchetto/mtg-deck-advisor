"""Role tags for a pool, on demand: cached tags reused, only new or stale cards tagged.

Cards are tagged only when a user's pool needs them, and the tags are stored,
so each card is paid for once and then shared by every later pool. A card is
tagged again only when its stored tags are stale: its text has changed (a new
content hash) or the taxonomy has (a new prompt version).

Batches run a few at a time in parallel threads. Each runs in a copy of the
caller's context, so its model calls keep the caller's trace ID (thread pools
don't copy context by themselves; see ADR 0001). Database writes stay on the
calling thread, because a connection mustn't be shared between threads.
"""

import contextvars
from collections.abc import Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from uuid import UUID

import psycopg
import structlog

from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.errors import BudgetExceededError, ModelError
from mtg_deck_advisor.llm.pricing import cost_usd
from mtg_deck_advisor.llm.roles import (
    PROMPT_VERSION,
    CardRoles,
    CardToTag,
    TaggedCard,
    build_request,
    load_roles,
    save_roles,
    tag_cards,
)
from mtg_deck_advisor.llm.types import Usage

log = structlog.get_logger(__name__)

DEFAULT_BATCH_SIZE = 25
DEFAULT_PARALLEL = 4
# For the up-front projection: about 4 characters per input token, and about
# 55 output tokens per card (measured: Opus 5.5 used about 39).
CHARS_PER_TOKEN = 4
EXPECTED_OUTPUT_TOKENS_PER_CARD = 55


@dataclass(frozen=True)
class TaggingResult:
    roles: dict[UUID, CardRoles]
    # Cards tagged by this call, and cards whose stored tags were reused.
    tagged: int
    cached: int
    # Cards in batches that failed: untagged for now, and tagged on a later call.
    failed: list[UUID]
    # Requested cards that don't exist or have been removed.
    unknown: list[UUID]
    cost_usd: float


def projected_cost_usd(client: ModelClient, batches: Sequence[Sequence[CardToTag]]) -> float:
    """What tagging these batches is expected to cost (not the worst case)."""
    total = 0.0
    for batch in batches:
        request = build_request(batch)
        chars = len(request.system) + len(request.messages[0].content)
        usage = Usage(
            input_tokens=chars // CHARS_PER_TOKEN,
            output_tokens=EXPECTED_OUTPUT_TOKENS_PER_CARD * len(batch),
        )
        total += cost_usd(client.model, usage)
    return total


def roles_for(
    conn: psycopg.Connection,
    client: ModelClient,
    oracle_ids: Iterable[UUID],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    parallel: int = DEFAULT_PARALLEL,
) -> TaggingResult:
    """Roles for every requested card, tagging only the ones without fresh stored tags."""
    requested = list(dict.fromkeys(oracle_ids))
    rows = conn.execute(
        """
        SELECT oracle_id, name, type_line, mana_cost, oracle_text, content_hash
        FROM cards WHERE removed_at IS NULL AND oracle_id = ANY(%s)
        """,
        (requested,),
    ).fetchall()
    cards = {
        row[0]: CardToTag(
            oracle_id=row[0], name=row[1], type_line=row[2], mana_cost=row[3], oracle_text=row[4]
        )
        for row in rows
    }
    hashes: dict[UUID, str] = {row[0]: row[5] for row in rows}
    unknown = [oracle_id for oracle_id in requested if oracle_id not in cards]

    fresh = {
        oracle_id: stored
        for oracle_id, stored in load_roles(conn, cards).items()
        if stored.content_hash == hashes[oracle_id] and stored.prompt_version == PROMPT_VERSION
    }
    to_tag = [
        cards[oracle_id] for oracle_id in requested if oracle_id in cards and oracle_id not in fresh
    ]
    batches = [to_tag[i : i + batch_size] for i in range(0, len(to_tag), batch_size)]

    remaining = client.remaining_usd
    if batches and remaining is not None:
        projected = projected_cost_usd(client, batches)
        if projected > remaining:
            raise BudgetExceededError(
                f"tagging {len(to_tag)} cards is projected to cost ${projected:.2f}, more than "
                f"the ${remaining:.2f} left under the cost cap (RUN_COST_CAP_USD)"
            )

    spent_before = client.spent_usd
    tagged: dict[UUID, CardRoles] = {}
    failed: list[UUID] = []
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures: dict[Future[dict[UUID, TaggedCard]], list[CardToTag]] = {
            pool.submit(contextvars.copy_context().run, tag_cards, client, batch): batch
            for batch in batches
        }
        for future in as_completed(futures):
            batch = futures[future]
            try:
                answers = future.result()
            except ModelError as exc:
                log.warning("role_tagging_batch_failed", cards=len(batch), error=str(exc))
                failed += [card.oracle_id for card in batch]
                continue
            results = [
                CardRoles(
                    oracle_id=oracle_id,
                    roles=answer.roles,
                    reason=answer.reason,
                    content_hash=hashes[oracle_id],
                    prompt_version=PROMPT_VERSION,
                    model=client.model,
                )
                for oracle_id, answer in answers.items()
            ]
            save_roles(conn, results)  # saved per batch, so a later failure loses nothing
            tagged |= {result.oracle_id: result for result in results}

    order = {oracle_id: position for position, oracle_id in enumerate(requested)}
    result = TaggingResult(
        roles=fresh | tagged,
        tagged=len(tagged),
        cached=len(fresh),
        failed=sorted(failed, key=order.__getitem__),
        unknown=unknown,
        cost_usd=client.spent_usd - spent_before,
    )
    log.info(
        "role_tagging_finished",
        cards=len(cards),
        tagged=result.tagged,
        cached=result.cached,
        failed=len(result.failed),
        cost_usd=round(result.cost_usd, 4),
    )
    return result
