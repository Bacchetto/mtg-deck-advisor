# 0006 - Content hashes over normalised fields, and soft deletes, for ingestion

**Status:** Accepted, 2026-10-01
**Applies to:** `mtg_deck_advisor.ingestion` (`cards`, `sync`, `card_ingestion`), the `cards` table

## Context

ING-3 requires idempotent ingestion: re-running it creates no duplicates and doesn't re-embed unchanged records. Its acceptance test is that two runs in a row re-process zero records the second time. ING-4 asks for counts of added, updated, unchanged and removed records.

Scryfall's `oracle_cards` file is regenerated daily with about 38,700 entries. Each entry describes one card (its oracle identity), but also attaches one "most recognisable" printing, along with that printing's set, links, prices, images and an EDHREC rank. Prices and ranks change every day, and the chosen printing changes whenever a card is reprinted. From Milestone 4, every changed card is re-embedded, which costs time and possibly money.

Cards also occasionally disappear from the data, and later milestones will reference cards from saved decks and audit logs.

## Decision

- **Store only oracle-level fields** (name, cost, type, rules text, colors, color identity, keywords, Commander legality and so on), and hash exactly those: the SHA-256 of the normalised record's canonical JSON, with sorted keys. Printing-specific and daily-changing fields aren't stored at all, so they can't trigger an update.
- **Compare hashes, then write only differences.** A pure `diff()` classifies each record. One transaction then:
  - `COPY`s new cards in
  - updates changed ones
  - marks missing ones removed
  - leaves unchanged rows untouched, so they aren't even rewritten with equal values
- **Soft delete.** A card missing from the source gets `removed_at` set rather than being deleted. If it reappears it's restored, which clears `removed_at` and counts as added.
- **Record every run** in `ingestion_runs`, with the source version, counts and trace ID. The row is committed before the data is applied, so a failed run leaves a record without `finished_at`.

## Alternatives

**Hash the raw JSON.** No normalisation to maintain, but every run would mark nearly every card "updated", because prices and images change daily. Idempotence would exist in name only.

**Upsert everything with `INSERT ... ON CONFLICT DO UPDATE`.** One statement and no diff code, but it rewrites all ~35,000 rows on every run: write load, table bloat, and no way to tell "updated" from "unchanged" without comparing anyway. Adding `WHERE cards.content_hash <> excluded.content_hash` solves the rewrite, but not removal detection or the counts.

**Hard delete.** A simpler table, but a deck or audit entry that references a vanished card would break, or need cascading deletes that destroy history.

**Store the raw JSON in a `jsonb` column** for future needs. Easy to add later if a feature needs a field. Storing it now means about 100 MB of mostly unused data, plus the question of whether it belongs in the hash.

## Consequences

- Against the real 2026-10-01 file: the first run added 34,664 cards in about 3 s (6 s end to end). A second run reported 0 added, 0 updated, 34,664 unchanged and 0 removed, and an integration test confirms it rewrites no row (each row's `xmin` is unchanged).
- Milestone 4 can re-embed exactly the cards whose `content_hash` changed.
- Normalisation is code to maintain. If Scryfall adds a field this project starts to need, the record and hash change, and the next run updates every card once. That's correct, since the stored data really did change.
- Queries that serve users must filter `removed_at IS NULL`.
- No Scryfall links are stored. A link can be built from `oracle_id` when needed (Scryfall search by oracle ID).
