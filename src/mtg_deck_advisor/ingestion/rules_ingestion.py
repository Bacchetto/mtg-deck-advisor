"""Loading the Comprehensive Rules into the database, idempotently (ING-1, ING-3).

The rules file comes from a pinned URL (`RULES_URL`), so moving to a new
edition is a deliberate settings change, and eval results stay tied to a known
version of the rules. The download goes through the same cache as Scryfall's
files, and the same content-hash diff decides what to write (ADR 0006).
"""

from collections.abc import Iterable

import httpx2
import psycopg
import structlog

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.ingestion.downloads import download_to_cache
from mtg_deck_advisor.ingestion.rules import RuleRecord, parse_rules
from mtg_deck_advisor.ingestion.runs import finish_run, start_run
from mtg_deck_advisor.ingestion.store import Row, apply_changes
from mtg_deck_advisor.ingestion.sync import IngestionReport, content_hash
from mtg_deck_advisor.observability.tracing import traced

log = structlog.get_logger(__name__)

SOURCE = "comprehensive_rules"
# Table column order, the key first.
RULE_FIELDS = ("number", "parent", "section", "text")


def apply_rules(conn: psycopg.Connection, rules: Iterable[RuleRecord]) -> IngestionReport:
    """Bring the rules table in line with `rules`, in one transaction."""
    incoming: dict[str, Row] = {
        rule.number: (tuple(getattr(rule, name) for name in RULE_FIELDS), content_hash(rule))
        for rule in rules
    }
    return apply_changes(conn, table="rules", columns=RULE_FIELDS, incoming=incoming)


def ingest_rules(settings: Settings, client: httpx2.Client) -> IngestionReport:
    """Fetch the pinned rules file (through the cache), parse it and apply it."""
    with traced() as trace_id:
        path = download_to_cache(client, settings.rules_url, settings.cache_dir)
        # utf-8-sig: tolerate a byte-order mark if a future edition has one.
        parsed = parse_rules(path.read_text(encoding="utf-8-sig"))
        source_version = parsed.effective_date.isoformat()
        with connect(settings) as conn:
            run_id = start_run(conn, SOURCE, source_version, trace_id)
            report = apply_rules(conn, parsed.rules)
            finish_run(conn, run_id, report)
        log.info("rules_ingested", source_version=source_version, **report.model_dump())
        return report
