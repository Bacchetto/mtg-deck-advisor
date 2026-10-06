"""`mtg-advisor build`: the whole build in one interactive command (#108).

The command is a client of the API like every other: it drafts, shows the
proposal, and loops on what the user types: approve (and apply), reject,
change, export, quit. These tests drive it with scripted answers against the
real app.
"""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

import io
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from mtg_deck_advisor.cli import main
from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.guardrails.audit import audit_entries
from tests.integration.test_agent_tools import ATRAXA, loaded  # noqa: F401
from tests.integration.test_api_proposals import CHANGES, ok
from tests.integration.test_api_runs import (
    DRAFT_SCRIPT,
    POOL_TEXT,
    add_pool,
    answer,
    app_for,  # noqa: F401 (a fixture)
)


class Session:
    """One `mtg-advisor build` run, answering its prompts from a script."""

    def __init__(self, client: TestClient, answers: Iterable[str]) -> None:
        self.client = client
        self.answers = list(answers)
        self.prompts: list[str] = []

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError  # what input() raises at the end of input
        return self.answers.pop(0)

    def run(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = main(
            ["build", *args],
            http=self.client,
            out=out,
            err=err,
            sleep=lambda _: None,
            ask=self.ask,
        )
        return code, out.getvalue(), err.getvalue()


def pool_file(tmp_path: Path) -> str:
    path = tmp_path / "binder.txt"
    path.write_text(POOL_TEXT, encoding="utf-8")
    return str(path)


def decks(client: TestClient) -> list[dict[str, Any]]:
    listing: list[dict[str, Any]] = ok(client.get("/decks", params={"include_archived": "true"}))
    return listing


def test_a_deck_is_built_from_a_pool_file_to_an_exported_decklist(
    app_for: Callable[..., TestClient], loaded: Settings, tmp_path: Path
) -> None:
    client = app_for(*DRAFT_SCRIPT, CHANGES, answer("Swapped."))
    exported = tmp_path / "rats.txt"
    session = Session(
        client,
        [
            "Rats.",  # what the user wants
            "a",  # approve and apply the draft
            "c",
            "A flier.",  # the change wanted
            "a",  # approve and apply the change
            "e",
            str(exported),  # where to save the decklist
            "q",
        ],
    )

    code, out, err = session.run(pool_file(tmp_path))

    assert code == 0, err
    assert "64 cards" in out  # the pool was submitted
    assert "Rationale: Rats." in out and f"1 {ATRAXA}" in out  # the draft was shown
    assert "version 1" in out and "version 2" in out
    assert "+ 1 Delver of Secrets" in out and "- 1 Island" in out  # the change was shown
    lines = exported.read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"1 {ATRAXA}" and "1 Delver of Secrets // Insectile Aberration" in lines
    (deck,) = decks(client)
    assert (deck["name"], deck["version"]) == (ATRAXA, 2)
    with connect(loaded) as conn:
        actions = [e.action for e in audit_entries(conn, f"deck:{deck['id']}")]
    assert actions[-2:] == ["approve_export", "export"]


def test_a_rejected_draft_is_drafted_again_into_the_same_deck(
    app_for: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = app_for(*DRAFT_SCRIPT, *DRAFT_SCRIPT)
    pool = add_pool(client)
    session = Session(client, ["", "r", "Fewer rats.", "a", "q"])

    code, _, err = session.run(pool)

    assert code == 0, err
    (deck,) = decks(client)  # no empty deck left behind
    assert deck["version"] == 1
    first, second = ok(client.get(f"/decks/{deck['id']}/proposals"))
    assert (first["status"], second["status"]) == ("rejected", "applied")
    assert ok(client.get(f"/proposals/{first['id']}"))["kind"] == "deck"


def test_quitting_leaves_the_proposal_pending_and_says_how_to_continue(
    app_for: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    pool = add_pool(client)

    code, out, _ = Session(client, ["", "q"]).run(pool)

    (deck,) = decks(client)
    (proposal,) = ok(client.get(f"/decks/{deck['id']}/proposals"))
    assert code == 0 and proposal["status"] == "pending"
    assert f"mtg-advisor build --deck {deck['id']}" in out


def test_a_build_continues_an_existing_deck_from_its_pending_proposal(
    app_for: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    Session(client, ["", "q"]).run(add_pool(client))
    (deck,) = decks(client)

    code, out, err = Session(client, ["a", "q"]).run("--deck", deck["id"])

    assert code == 0, err
    assert "Rationale: Rats." in out
    assert decks(client)[0]["version"] == 1


def test_a_pool_is_chosen_from_the_list_when_none_is_given(
    app_for: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    add_pool(client)
    session = Session(client, ["1", "", "q"])

    code, out, err = session.run()

    assert code == 0, err
    assert "1) Binder" in out
    assert len(decks(client)) == 1


def test_the_end_of_input_quits_cleanly(app_for: Callable[..., TestClient], tmp_path: Path) -> None:
    client = app_for(*DRAFT_SCRIPT)

    code, _, err = Session(client, [""]).run(add_pool(client))

    assert code == 0, err
    (deck,) = decks(client)
    assert deck["version"] is None


def test_an_unknown_choice_is_asked_again(
    app_for: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    session = Session(client, ["", "x", "e", "q"])  # nothing saved, so no export yet

    code, out, _ = session.run(add_pool(client))

    assert code == 0
    assert out.count("[a]pprove") >= 2
    assert "nothing saved to export yet" in out


def test_the_request_prompt_says_what_to_give(
    app_for: Callable[..., TestClient], tmp_path: Path
) -> None:
    session = Session(app_for(*DRAFT_SCRIPT), ["", "q"])

    session.run(add_pool(session.client))

    (prompt,) = [p for p in session.prompts if "built around" in p]
    assert "Name a commander or a theme" in prompt
    assert "? to see" in prompt and "Enter to let the agent choose" in prompt


def test_a_question_mark_lists_the_pools_commanders_then_asks_again(
    app_for: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    session = Session(client, ["?", "", "q"])

    code, out, err = session.run(add_pool(client))

    assert code == 0, err
    assert f"{ATRAXA} (WUBG)" in out
    assert len([p for p in session.prompts if "built around" in p]) == 2
    (deck,) = decks(client)  # one draft, after the list
    assert deck["version"] is None


def test_a_pool_with_no_possible_commander_says_so(
    app_for: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = app_for()
    created = ok(client.post("/pools", json={"name": "Lands", "content": "40 Plains\n"}), 201)
    session = Session(client, ["?"])

    _, out, _ = session.run(str(created["pool_id"]))

    assert "no card in this pool can be a commander" in out


# --- the API steps the build needs ---------------------------------------------------------


def test_a_decks_proposals_are_listed(app_for: Callable[..., TestClient]) -> None:
    client = app_for(*DRAFT_SCRIPT)
    started = ok(client.post(f"/pools/{add_pool(client)}/drafts", json={}), 202)

    (proposal,) = ok(client.get(f"/decks/{started['deck_id']}/proposals"))

    assert (proposal["kind"], proposal["status"]) == ("deck", "pending")
    assert client.get("/decks/00000000-0000-0000-0000-000000000000/proposals").status_code == 404


def test_only_a_deck_with_nothing_saved_can_be_drafted_again(
    app_for: Callable[..., TestClient],
) -> None:
    client = app_for(*DRAFT_SCRIPT, *DRAFT_SCRIPT)
    started = ok(client.post(f"/pools/{add_pool(client)}/drafts", json={}), 202)
    deck_id = started["deck_id"]
    (first,) = ok(client.get(f"/decks/{deck_id}/proposals"))

    again = ok(client.post(f"/decks/{deck_id}/drafts", json={"request": "More."}), 202)
    ok(client.post(f"/proposals/{first['id']}/approval", json={}))
    ok(client.post(f"/proposals/{first['id']}/application"))
    saved = client.post(f"/decks/{deck_id}/drafts", json={})

    assert again["deck_id"] == deck_id
    assert len(ok(client.get(f"/decks/{deck_id}/proposals"))) == 2
    assert saved.status_code == 409 and "refine" in saved.json()["detail"]
