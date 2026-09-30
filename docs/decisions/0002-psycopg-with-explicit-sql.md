# 0002 - psycopg 3 with explicit SQL, no ORM

**Status:** Accepted, 2026-09-30
**Applies to:** `mtg_deck_advisor.db` and every module that queries the database

## Context

The database work in this project is dominated by queries an ORM expresses poorly:

- **Vector similarity** with pgvector operators (`<=>` for cosine distance), plus exact metadata filters in the same query (RAG-1).
- **Full-text ranking** with `tsvector` and `ts_rank`, and reciprocal rank fusion of the two result lists for hybrid search (RAG-5).
- **Idempotent ingestion**: `INSERT ... ON CONFLICT` upserts that skip records whose content hash hasn't changed, and report per-row outcomes as added, updated or unchanged counts (ING-3, ING-4).

The remaining tables (decks, proposals, approvals, the audit log) are small and simple.

## Decision

**psycopg 3, with SQL written out in the code that runs it.** No ORM models and no query builder. `db.connection.connect()` opens connections with a 5-second connect timeout, so an unreachable database fails fast. Rows come back as tuples, or as Pydantic models where a typed shape helps.

## Alternatives

**SQLAlchemy ORM.** Less boilerplate for simple CRUD, and familiar to many Python teams. Rejected because the queries that matter would be raw SQL or `text()` anyway. pgvector operators, rank fusion and `ON CONFLICT` with per-row outcomes all fight the ORM, and the result would be two persistence styles in one codebase.

**SQLAlchemy Core (query builder, no ORM).** Composable queries without models. But the vector and ranking queries would still be mostly `text()` fragments, and a reader has to translate builder calls back into SQL to review them. Plain SQL is what gets reviewed, run in `psql`, and checked with `EXPLAIN ANALYZE`.

**asyncpg.** Faster for async workloads. psycopg 3 supports both sync and async with one API, has first-class pgvector adapter support, and is the driver Alembic can share through SQLAlchemy (ADR 0003).

## Consequences

- Every query the application runs is visible, reviewable and runnable as-is in `psql`.
- Mapping rows to objects is done by hand. For this schema, that's a small cost.
- SQLAlchemy is still installed, because Alembic depends on it (ADR 0003). It's used only by migrations, never by application queries. A reviewer seeing it in the dependencies should find this note.
- SQL written in strings is not type-checked. Integration tests against a real database (ENG-3) are what catch a broken query.
