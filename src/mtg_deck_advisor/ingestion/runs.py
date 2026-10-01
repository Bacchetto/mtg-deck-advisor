"""The ingestion_runs table: one row per run, for ING-4 and for tracing.

A run's row is committed when the run starts, before any data is applied, so a
run that fails part-way still leaves a record: one with no finished_at.
"""

import psycopg

from mtg_deck_advisor.ingestion.sync import IngestionReport


def start_run(conn: psycopg.Connection, source: str, source_version: str, trace_id: str) -> int:
    with conn.transaction():
        row = conn.execute(
            """
            INSERT INTO ingestion_runs (source, source_version, trace_id, started_at)
            VALUES (%s, %s, %s, now())
            RETURNING id
            """,
            (source, source_version, trace_id),
        ).fetchone()
    if row is None:  # INSERT ... RETURNING always returns a row
        raise RuntimeError("ingestion run was not recorded")
    run_id: int = row[0]
    return run_id


def finish_run(conn: psycopg.Connection, run_id: int, report: IngestionReport) -> None:
    with conn.transaction():
        conn.execute(
            """
            UPDATE ingestion_runs
            SET finished_at = now(), added = %s, updated = %s, unchanged = %s, removed = %s
            WHERE id = %s
            """,
            (report.added, report.updated, report.unchanged, report.removed, run_id),
        )
