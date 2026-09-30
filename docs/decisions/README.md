# Architecture Decision Records

A short record of every non-obvious choice in this project: what the situation was, what was decided, what else was considered, and what it costs.

"Why did you do it that way?" is easier to answer well when the reasoning was written down at the time rather than reconstructed months later.

## Format

One file per decision, named `NNNN-short-title.md`, numbered in the order they are recorded. Numbers are never reused and files are never deleted. A decision that gets reversed is superseded by a new ADR that says so, and the old one stays as a record of what was believed then.

Each ADR has four sections:

| Section | Answers |
|---|---|
| **Context** | What forced a decision? What constraints applied? |
| **Decision** | What was chosen, stated plainly. |
| **Alternatives** | What else was on the table, and why it lost. |
| **Consequences** | What this buys, and what it costs. Include the bad. |

Keep them short. A page is plenty.

Each ADR is linked from the module docstring of the code it governs, so someone reading that code finds the reasoning at the moment they need it.

## Index

| # | Decision | Applies to |
|---|---|---|
| [0001](0001-structured-logging-with-structlog.md) | Structured JSON logging with structlog, standard-library logs included | `observability` |
| [0002](0002-psycopg-with-explicit-sql.md) | psycopg 3 with explicit SQL, no ORM | `db`, all queries |
| [0003](0003-alembic-raw-sql-migrations-as-a-separate-step.md) | Alembic with raw SQL migrations, run as a separate step | `db.migrate`, migrations |
