"""The CLI driven against the real app, with a scripted model (#101)."""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from mtg_deck_advisor.cli import main
from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.guardrails.audit import audit_entries
from mtg_deck_advisor.llm.fake import tool_call_reply
from tests.integration.test_agent_tools import ATRAXA, LEGAL_CARDS, loaded  # noqa: F401
from tests.integration.test_api_proposals import CHANGES
from tests.integration.test_api_runs import (
    DRAFT_SCRIPT,
    POOL_TEXT,
    SONNET,
    answer,
    app_for,  # noqa: F401 (a fixture)
    call,
)


class Cli:
    """Runs CLI commands against one app, capturing what they print."""

    def __init__(self, client: TestClient, tmp_path: Path) -> None:
        self.client = client
        self.tmp_path = tmp_path

    def __call__(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = main(list(args), http=self.client, out=out, err=err, sleep=lambda _: None)
        return code, out.getvalue(), err.getvalue()

    def json(self, *args: str) -> Any:
        code, out, err = self("--json", *args)
        assert code == 0, err
        return json.loads(out)

    def pool_file(self) -> str:
        path = self.tmp_path / "pool.txt"
        path.write_text(POOL_TEXT, encoding="utf-8")
        return str(path)


def test_the_whole_flow_works_through_the_cli(
    app_for: Callable[..., TestClient], loaded: Settings, tmp_path: Path
) -> None:
    cli = Cli(app_for(*DRAFT_SCRIPT, CHANGES, answer("Swapped.")), tmp_path)

    pool = cli.json("pool", "add", cli.pool_file(), "--name", "Binder")
    run = cli.json("draft", pool["pool_id"], "--request", "Rats.")
    deck_id = run["deck_id"]
    (proposal,) = run["proposals"]
    cli.json("approve", proposal["id"], "--note", "Good.")
    assert cli.json("apply", proposal["id"]) == {"deck_id": deck_id, "version": 1}
    refine = cli.json("refine", deck_id, "--request", "A flier.")
    (change,) = refine["proposals"]
    cli.json("approve", change["id"])
    assert cli.json("apply", change["id"])["version"] == 2
    export_approval = cli.json("approve-export", deck_id)
    code, decklist, _ = cli("export", deck_id)

    assert pool["unresolved"] == ["Not A Real Card"]
    assert (run["status"], refine["status"]) == ("completed", "completed")
    assert export_approval["deck_version"] == 2  # the latest version, by default
    assert code == 0
    lines = decklist.splitlines()
    assert lines[0] == f"1 {ATRAXA}" and sum(int(line.split(" ", 1)[0]) for line in lines) == 100
    with connect(loaded) as conn:
        assert [e.action for e in audit_entries(conn, f"deck:{deck_id}")] == [
            "approve_export",
            "export",
        ]


def test_a_draft_reports_its_result_and_what_to_do_next(
    app_for: Callable[..., TestClient], tmp_path: Path
) -> None:
    cli = Cli(app_for(*DRAFT_SCRIPT), tmp_path)
    pool_id = cli.json("pool", "add", cli.pool_file())["pool_id"]

    code, out, _ = cli("draft", pool_id)

    assert code == 0
    assert "completed after 2 turns" in out
    assert "Drafted Atraxa and the rats." in out
    assert "deck proposal" in out and "pending" in out
    assert "mtg-advisor proposal " in out  # the next step


def test_a_proposal_and_a_deck_are_shown_readably(
    app_for: Callable[..., TestClient], tmp_path: Path
) -> None:
    cli = Cli(
        app_for(
            tool_call_reply(
                call("propose_deck", commander=ATRAXA, cards=LEGAL_CARDS[1:], rationale="Few."),
                model=SONNET,
            ),
            *DRAFT_SCRIPT,
        ),
        tmp_path,
    )
    run = cli.json("draft", cli.json("pool", "add", cli.pool_file())["pool_id"])
    invalid, valid = run["proposals"]

    _, rejected, _ = cli("proposal", invalid["id"])
    _, legal, _ = cli("proposal", valid["id"])
    _, deck, _ = cli("deck", run["deck_id"])

    assert "invalid" in rejected and "903.5a" in rejected and "40 cards" in rejected
    assert "Rationale: Rats." in legal and f"1 {ATRAXA}" in legal
    assert "no saved version yet" in deck


def test_a_refused_step_exits_non_zero_with_the_reason(
    app_for: Callable[..., TestClient], tmp_path: Path
) -> None:
    cli = Cli(app_for(*DRAFT_SCRIPT), tmp_path)
    run = cli.json("draft", cli.json("pool", "add", cli.pool_file())["pool_id"])
    (proposal,) = run["proposals"]

    code, out, err = cli("apply", proposal["id"])

    assert code == 1 and out == ""
    assert "403" in err and "no approval" in err


def test_a_rules_question_is_answered_with_its_citations(
    app_for: Callable[..., TestClient], tmp_path: Path
) -> None:
    cli = Cli(
        app_for(
            tool_call_reply(
                call("search_rules", question="color identity commander", k=3), model=SONNET
            ),
            answer("Cards must match the commander's colors.\nCitations: 903.4"),
            answer("NOT FOUND. Nothing on proxies."),
        ),
        tmp_path,
    )

    code, out, _ = cli("ask", "What is color identity?")
    _, not_found, _ = cli("ask", "Proxies?")

    assert code == 0
    assert "Cards must match the commander's colors." in out and "[903.4]" in out
    assert "Not found" in not_found and "Nothing on proxies" in not_found
