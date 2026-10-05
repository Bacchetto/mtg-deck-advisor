"""The user's steps over HTTP: read a proposal, decide, apply, export (#100; GRD-2, GRD-5)."""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.guardrails.audit import audit_entries
from mtg_deck_advisor.llm.fake import tool_call_reply
from tests.integration.test_agent_tools import ATRAXA, LEGAL_CARDS
from tests.integration.test_api_runs import (
    DRAFT_SCRIPT,
    SONNET,
    add_pool,
    answer,
    app_for,  # noqa: F401 (a fixture)
    call,
    draft,
    loaded,  # noqa: F401 (a fixture)
)

CHANGES = tool_call_reply(
    call(
        "propose_changes",
        add=[{"name": "Delver of Secrets"}],
        remove=[{"name": "Island"}],
        rationale="A flier.",
    ),
    model=SONNET,
)


def drafted(client: TestClient) -> tuple[str, str]:
    """A finished draft run's deck and its pending proposal."""
    started = draft(client, add_pool(client))
    (proposal,) = client.get(f"/runs/{started['run_id']}").json()["proposals"]
    return str(started["deck_id"]), str(proposal["id"])


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json()


def actions(settings: Settings, subject: str) -> list[str]:
    with connect(settings) as conn:
        return [entry.action for entry in audit_entries(conn, subject)]


# --- reading ---------------------------------------------------------------------------


def test_a_proposal_shows_its_reasoning_and_resulting_decklist(
    app_for: Callable[..., TestClient],
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    deck_id, proposal_id = drafted(client)

    proposal = ok(client.get(f"/proposals/{proposal_id}"))

    assert (proposal["kind"], proposal["status"], proposal["deck_id"]) == (
        "deck",
        "pending",
        deck_id,
    )
    assert proposal["rationale"] == "Rats."
    assert proposal["problems"] == []
    assert proposal["decklist"].splitlines()[0] == f"1 {ATRAXA}"
    assert "60 Relentless Rats" in proposal["decklist"]
    assert proposal["changes"] is None
    assert client.get(f"/proposals/{uuid4()}").status_code == 404


def test_an_invalid_proposal_shows_every_problem(app_for: Callable[..., TestClient]) -> None:
    client = app_for(
        tool_call_reply(
            call("propose_deck", commander=ATRAXA, cards=LEGAL_CARDS[1:], rationale="r"),
            model=SONNET,
        ),
        answer("Gave up."),
    )
    _, proposal_id = drafted(client)

    proposal = ok(client.get(f"/proposals/{proposal_id}"))

    assert proposal["status"] == "invalid"
    (problem,) = proposal["problems"]
    assert problem["rule"] == "903.5a" and "40 cards" in problem["message"]


# --- deciding, applying, exporting --------------------------------------------------------


def test_the_full_flow_approve_apply_refine_export_works_over_http(
    app_for: Callable[..., TestClient],
    loaded: Settings,
) -> None:
    client = app_for(*DRAFT_SCRIPT, CHANGES, answer("Swapped."))
    deck_id, proposal_id = drafted(client)

    approval = ok(client.post(f"/proposals/{proposal_id}/approval", json={"note": "Good."}))
    applied = ok(client.post(f"/proposals/{proposal_id}/application"))
    refine = ok(client.post(f"/decks/{deck_id}/refinements", json={"request": "A flier."}), 202)
    (change,) = client.get(f"/runs/{refine['run_id']}").json()["proposals"]
    shown = ok(client.get(f"/proposals/{change['id']}"))
    ok(client.post(f"/proposals/{change['id']}/approval", json={}))
    second = ok(client.post(f"/proposals/{change['id']}/application"))
    export_approval = ok(client.post(f"/decks/{deck_id}/versions/2/export-approval", json={}))
    exported = client.get(f"/decks/{deck_id}/versions/2/export")

    assert (approval["decision"], approval["actor"], approval["note"]) == (
        "approved",
        "user",
        "Good.",
    )
    assert applied == {"deck_id": deck_id, "version": 1}
    assert shown["changes"] == {
        "add": [{"name": "Delver of Secrets // Insectile Aberration", "count": 1}],
        "remove": [{"name": "Island", "count": 1}],
    }
    assert second == {"deck_id": deck_id, "version": 2}
    assert (export_approval["kind"], export_approval["deck_version"]) == ("export", 2)
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/plain")
    lines = exported.text.splitlines()
    assert lines[0] == f"1 {ATRAXA}" and sum(int(line.split(" ", 1)[0]) for line in lines) == 100
    assert actions(loaded, f"proposal:{proposal_id}") == ["propose", "approve", "apply"]
    assert actions(loaded, f"deck:{deck_id}") == ["approve_export", "export"]


def test_a_deck_shows_its_versions_and_latest_decklist(app_for: Callable[..., TestClient]) -> None:
    client = app_for(*DRAFT_SCRIPT)
    deck_id, proposal_id = drafted(client)
    empty = ok(client.get(f"/decks/{deck_id}"))
    ok(client.post(f"/proposals/{proposal_id}/approval", json={}))
    ok(client.post(f"/proposals/{proposal_id}/application"))

    deck = ok(client.get(f"/decks/{deck_id}"))

    assert (empty["version"], empty["decklist"], empty["versions"]) == (None, None, [])
    assert deck["version"] == 1
    assert [(v["version"], v["proposal_id"]) for v in deck["versions"]] == [(1, proposal_id)]
    assert deck["decklist"].startswith(f"1 {ATRAXA}")
    assert client.get(f"/decks/{uuid4()}").status_code == 404


# --- refusals ---------------------------------------------------------------------------


def test_apply_without_an_approval_is_403_and_changes_nothing(
    app_for: Callable[..., TestClient],
    loaded: Settings,
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    deck_id, proposal_id = drafted(client)

    response = client.post(f"/proposals/{proposal_id}/application")

    assert response.status_code == 403
    assert "no approval" in response.json()["detail"]
    assert ok(client.get(f"/decks/{deck_id}"))["version"] is None
    assert ok(client.get(f"/proposals/{proposal_id}"))["status"] == "pending"
    # The refusal's audit entry survives the failed request.
    assert actions(loaded, f"proposal:{proposal_id}") == ["propose", "apply_refused"]


def test_export_without_an_approval_is_403(
    app_for: Callable[..., TestClient],
    loaded: Settings,
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    deck_id, proposal_id = drafted(client)
    ok(client.post(f"/proposals/{proposal_id}/approval", json={}))
    ok(client.post(f"/proposals/{proposal_id}/application"))

    response = client.get(f"/decks/{deck_id}/versions/1/export")

    assert response.status_code == 403
    assert actions(loaded, f"deck:{deck_id}") == ["export_refused"]
    assert client.get(f"/decks/{deck_id}/versions/9/export").status_code == 404
    missing = client.post(f"/decks/{deck_id}/versions/9/export-approval", json={})
    assert missing.status_code == 404


def test_deciding_twice_or_applying_a_rejected_proposal_is_refused(
    app_for: Callable[..., TestClient],
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    _, proposal_id = drafted(client)

    rejected = ok(client.post(f"/proposals/{proposal_id}/rejection", json={"reason": "No rats."}))
    again = client.post(f"/proposals/{proposal_id}/approval", json={})
    applied = client.post(f"/proposals/{proposal_id}/application")
    no_reason = client.post(f"/proposals/{proposal_id}/rejection", json={})

    assert (rejected["decision"], rejected["note"]) == ("rejected", "No rats.")
    assert again.status_code == 409 and "rejected" in again.json()["detail"]
    assert applied.status_code == 403
    assert no_reason.status_code == 422
    assert client.post(f"/proposals/{uuid4()}/approval", json={}).status_code == 404


def test_a_stale_change_set_is_409(
    app_for: Callable[..., TestClient],
) -> None:
    client = app_for(*DRAFT_SCRIPT, CHANGES, CHANGES, answer("Two ideas."))
    deck_id, proposal_id = drafted(client)
    ok(client.post(f"/proposals/{proposal_id}/approval", json={}))
    ok(client.post(f"/proposals/{proposal_id}/application"))
    refine = ok(client.post(f"/decks/{deck_id}/refinements", json={"request": "x"}), 202)
    first, second = client.get(f"/runs/{refine['run_id']}").json()["proposals"]
    for proposal in (first, second):
        ok(client.post(f"/proposals/{proposal['id']}/approval", json={}))
    ok(client.post(f"/proposals/{first['id']}/application"))

    response = client.post(f"/proposals/{second['id']}/application")

    assert response.status_code == 409
    assert "version 1" in response.json()["detail"]


# --- documentation (ENG-5) -----------------------------------------------------------------


def test_the_openapi_document_covers_every_endpoint(app_for: Callable[..., TestClient]) -> None:
    paths = app_for().get("/openapi.json").json()["paths"]

    expected = {
        "/pools": {"get", "post"},
        "/pools/{pool_id}": {"get"},
        "/pools/{pool_id}/drafts": {"post"},
        "/decks/{deck_id}": {"get"},
        "/decks/{deck_id}/refinements": {"post"},
        "/decks/{deck_id}/versions/{version}/export-approval": {"post"},
        "/decks/{deck_id}/versions/{version}/export": {"get"},
        "/runs/{run_id}": {"get"},
        "/rules/answers": {"post"},
        "/proposals/{proposal_id}": {"get"},
        "/proposals/{proposal_id}/approval": {"post"},
        "/proposals/{proposal_id}/rejection": {"post"},
        "/proposals/{proposal_id}/application": {"post"},
        "/health": {"get"},
    }
    for path, methods in expected.items():
        assert set(paths[path]) == methods, path
        for method in methods:
            operation = paths[path][method]
            assert operation.get("summary") and operation.get("description"), (path, method)
