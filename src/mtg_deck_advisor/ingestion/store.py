"""Applying a content-hash diff to a table, shared by every ingested source.

Each source table has a key column, its data columns, `content_hash`,
`updated_at` and `removed_at`. Only differences are written: new rows are
COPYed in, changed or returning rows updated, missing rows marked removed,
and unchanged rows not touched at all. See ADR 0006.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import psycopg
from psycopg import sql

from mtg_deck_advisor.ingestion.sync import Existing, IngestionReport, diff

# A row's values, in the order of `columns` (key first), and its content hash.
Row = tuple[tuple[Any, ...], str]


def apply_changes[K](
    conn: psycopg.Connection,
    *,
    table: str,
    columns: Sequence[str],
    incoming: Mapping[K, Row],
) -> IngestionReport:
    """Bring `table` in line with `incoming`, in one transaction.

    `columns[0]` is the key column. Table and column names are composed as SQL
    identifiers, never pasted into the SQL text, so the statements are
    injection-safe by construction.
    """
    key, *data_columns = columns
    all_columns = [*columns, "content_hash"]
    table_id, key_id = sql.Identifier(table), sql.Identifier(key)

    with conn.transaction():
        stored = conn.execute(
            sql.SQL("SELECT {}, content_hash, removed_at IS NOT NULL FROM {}").format(
                key_id, table_id
            )
        )
        existing = {row_key: Existing(row_hash, removed) for row_key, row_hash, removed in stored}
        changes = diff(existing, {k: row_hash for k, (_, row_hash) in incoming.items()})

        if changes.added:
            # COPY rather than INSERT: a first run loads tens of thousands of rows.
            copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(
                table_id, sql.SQL(", ").join(map(sql.Identifier, all_columns))
            )
            with conn.cursor().copy(copy_sql) as copy:
                for k in changes.added:
                    values, row_hash = incoming[k]
                    copy.write_row((*values, row_hash))

        rewrite = changes.updated + changes.restored
        if rewrite:
            assignments = sql.SQL(", ").join(
                sql.SQL("{} = %s").format(sql.Identifier(column))
                for column in [*data_columns, "content_hash"]
            )
            update_sql = sql.SQL(
                "UPDATE {} SET {}, updated_at = now(), removed_at = NULL WHERE {} = %s"
            ).format(table_id, assignments, key_id)
            conn.cursor().executemany(
                update_sql,
                [(*incoming[k][0][1:], incoming[k][1], k) for k in rewrite],
            )

        if changes.removed:
            conn.execute(
                sql.SQL("UPDATE {} SET removed_at = now() WHERE {} = ANY(%s)").format(
                    table_id, key_id
                ),
                (changes.removed,),
            )

    return changes.report()
