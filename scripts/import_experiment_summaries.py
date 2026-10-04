"""Copy the summaries and summary vectors made in the #74 experiment into the app's tables.

    python scripts/import_experiment_summaries.py

The experiment summarised all 32,116 cards with qwen3:14b (about 6.6 h of GPU
time) and embedded them with qwen3-embedding:8b into the `experiments`
schema. The app's tables (migration 0011) hold the same things, so this
copies them instead of redoing hours of work:

- summaries get the card's current content hash, so they count as current;
- vectors get the hash of the summary text, the embedding model's name and
  the summary text version, exactly as `embed_summaries` would store them.

Run `python -m mtg_deck_advisor.retrieval.embed summaries` afterwards: it
should write and embed nothing, which shows the copy matches what the app
would have made. Safe to rerun: existing rows are left alone.
"""

import sys

from mtg_deck_advisor.config import get_settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.retrieval.summaries import SUMMARY_PROMPT_VERSION
from mtg_deck_advisor.retrieval.text import SUMMARY_TEXT_VERSION

EXPERIMENT_MODEL = "qwen3:14b"
EXPERIMENT_PROMPT = "summaries-v1"
EXPERIMENT_VECTORS = "qwen3-embedding:8b|summary-only"
EMBEDDING_MODEL = "qwen3-embedding:8b"


def main() -> int:
    settings = get_settings()
    if settings.embedding_model != EMBEDDING_MODEL:
        print(
            f"EMBEDDING_MODEL is {settings.embedding_model}; the vectors are from "
            f"{EMBEDDING_MODEL}, so they'd never match a query. Not importing."
        )
        return 1
    if SUMMARY_PROMPT_VERSION != EXPERIMENT_PROMPT:
        print(
            f"the summary prompt is now {SUMMARY_PROMPT_VERSION}, not the experiment's "
            f"{EXPERIMENT_PROMPT}. Not importing."
        )
        return 1
    with connect(settings) as conn:
        summaries = conn.execute(
            """
            INSERT INTO card_summaries (oracle_id, summary, content_hash, prompt_version, model)
            SELECT s.oracle_id, s.summary, c.content_hash, s.prompt_version, s.model
            FROM experiments.card_summaries s JOIN cards c USING (oracle_id)
            WHERE s.prompt_version = %s AND s.model = %s
              AND c.removed_at IS NULL AND c.commander_legality = 'legal'
            ON CONFLICT (oracle_id) DO NOTHING
            """,
            (EXPERIMENT_PROMPT, EXPERIMENT_MODEL),
        ).rowcount
        vectors = conn.execute(
            """
            INSERT INTO summary_embeddings
                (oracle_id, embedding, content_hash, model, text_version)
            SELECT s.oracle_id, e.embedding,
                   encode(sha256(convert_to(s.summary, 'UTF8')), 'hex'), %s, %s
            FROM card_summaries s
            JOIN experiments.embeddings e
              ON e.model = %s AND e.kind = 'card' AND e.key = s.oracle_id::text
            ON CONFLICT (oracle_id) DO NOTHING
            """,
            (EMBEDDING_MODEL, SUMMARY_TEXT_VERSION, EXPERIMENT_VECTORS),
        ).rowcount
    print(f"imported {summaries:,} summaries and {vectors:,} summary vectors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
