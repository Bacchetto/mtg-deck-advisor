# 0003 - Alembic with raw SQL migrations, run as a separate step

**Status:** Accepted, 2026-09-30
**Applies to:** `mtg_deck_advisor.db.migrate`, `mtg_deck_advisor/db/migrations/`

## Context

The schema needs versioned migrations that run the same way locally, in tests and in a cloud deployment. With no ORM (ADR 0002), there are no Python models to generate migrations from. Every schema change is hand-written SQL.

Two questions: which tool tracks and applies migrations, and when do they run?

## Decision

**Alembic, with every migration written as raw SQL through `op.execute`, and autogenerate off** (`target_metadata = None`).

- **Migrations live inside the package** (`src/mtg_deck_advisor/db/migrations/`), so they ship in the wheel and the container image.
- **There is no `alembic.ini`.** `db.migrate.alembic_config()` builds the configuration in code, so there's one source of truth for the database URL: `Settings`. The URL goes into `Config.attributes` rather than a main option, because main options pass through ConfigParser, which reads `%` as interpolation syntax and would corrupt a URL-encoded password. A unit test covers this.
- **Revision IDs are sequential** (`0001`, `0002`, ...) rather than random hex, so migration order is obvious from the file names. A unit test asserts there is exactly one head, which catches two branches that each added a migration.
- **The run is one transaction.** Postgres has transactional DDL, so a failed migration leaves nothing half-applied.

**Migrations run as their own step, never on app startup.** `python -m mtg_deck_advisor.db.migrate` is run from the host during development, by a one-shot Compose service before the app starts (issue #17), and by a pre-deploy job in the cloud.

## Alternatives

**yoyo-migrations.** A small tool built around plain `.sql` files, and the closest match to Flyway. A reasonable choice, but Alembic is the tool most Python teams already know, has better long-term maintenance, and adds little cost here beyond its SQLAlchemy dependency.

**A hand-rolled runner** (a `schema_migrations` table plus sorted `.sql` files). About 50 lines, fully explainable, but it reinvents what Alembic already does: ordering, locking, downgrade support and a revision graph.

**Alembic autogenerate.** Generates migrations by diffing SQLAlchemy models against the database. It needs SQLAlchemy models, which this project deliberately doesn't have (ADR 0002), and its output needs hand review regardless.

**Migrating on app startup** (as grain bin did with Flyway). Simpler locally, but when several app instances start together they race to migrate, which needs a lock to be safe. Startup also becomes slow and able to fail for schema reasons. A separate step makes "migrate, then deploy" explicit and lets a failed migration stop a deploy before any new code runs.

## Consequences

- One migration tool that most Python reviewers recognise, with raw SQL that is readable on its own.
- SQLAlchemy is a runtime dependency used only by migrations (see ADR 0002).
- Someone has to run the migrate step. Locally that's documented in the README and Compose. In the cloud it must be wired into the deploy pipeline (milestone 9).
- There's no offline (SQL script) mode. `env.py` refuses it explicitly rather than half-supporting it.
- The Alembic engine uses the same 5-second connect timeout as the app. Without it, an unreachable host (such as `::1` under Rancher Desktop) waited out the operating system's TCP timeout of about two minutes before any error.
