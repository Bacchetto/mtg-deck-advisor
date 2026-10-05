"""The agent's flows over HTTP: pools, background draft and refine runs, rules answers (#99)."""

from collections.abc import Callable, Iterator
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from mtg_deck_advisor.agent.flows import AgentServices
from mtg_deck_advisor.api.app import create_app
from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.guardrails.approvals import apply_proposal, approve_proposal
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.errors import ModelCallError
from mtg_deck_advisor.llm.fake import FakeEmbedder, FakeProvider, tool_call_reply
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.types import ProviderResponse, ToolCall, Usage
from tests.integration.test_agent_tools import (
    ATRAXA,
    LEGAL_CARDS,
    loaded,  # noqa: F401 (a fixture)
)

SONNET = "claude-sonnet-5-5"
POOL_TEXT = (
    "1 Atraxa, Praetors' Voice\n60 Relentless Rats\n1 Sol Ring\n1 Lim-Dûl's Cohort\n"
    "1 Delver of Secrets\n1 Not A Real Card\n"
)


def call(tool: str, n: int = 1, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"toolu_{tool}_{n}", name=tool, arguments=arguments)


def answer(text: str) -> ProviderResponse:
    return ProviderResponse(
        text=text,
        stop_reason="end_turn",
        usage=Usage(input_tokens=100, output_tokens=20),
        model=SONNET,
    )


def scripted(
    *replies: ProviderResponse | Exception,
) -> Callable[[psycopg.Connection], AgentServices]:
    """A services factory whose model follows the script, shared across runs."""
    provider = FakeProvider(*replies)

    def services(conn: psycopg.Connection) -> AgentServices:
        client = ModelClient(provider, SONNET, recorder=MemoryRecorder(), cost_cap_usd=1.0)
        return AgentServices(conn=conn, client=client, embedder=FakeEmbedder(), max_turns=10)

    return services


@pytest.fixture
def app_for(loaded: Settings) -> Iterator[Callable[..., TestClient]]:  # noqa: F811
    clients: list[TestClient] = []

    def make(*replies: ProviderResponse | Exception) -> TestClient:
        client = TestClient(create_app(loaded, services=scripted(*replies)))
        clients.append(client.__enter__())
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def add_pool(client: TestClient) -> str:
    response = client.post("/pools", json={"name": "Binder", "content": POOL_TEXT})
    assert response.status_code == 201, response.text
    return str(response.json()["pool_id"])


def draft(client: TestClient, pool_id: str, request: str = "") -> dict[str, Any]:
    response = client.post(f"/pools/{pool_id}/drafts", json={"request": request})
    assert response.status_code == 202, response.text
    started: dict[str, Any] = response.json()
    assert response.headers["location"] == f"/runs/{started['run_id']}"
    return started


DRAFT_SCRIPT = (
    tool_call_reply(
        call("propose_deck", commander=ATRAXA, cards=LEGAL_CARDS, rationale="Rats."),
        model=SONNET,
    ),
    answer("Drafted Atraxa and the rats."),
)


# --- pools ---------------------------------------------------------------------------


def test_a_pool_is_submitted_and_its_unresolved_names_reported(
    app_for: Callable[..., TestClient],
) -> None:
    client = app_for()

    response = client.post("/pools", json={"name": "Binder", "content": POOL_TEXT})

    assert response.status_code == 201
    body = response.json()
    assert (body["cards"], body["distinct"]) == (64, 5)
    assert body["unresolved"] == ["Not A Real Card"]


def test_pools_are_listed_and_shown_with_their_possible_commanders(
    app_for: Callable[..., TestClient],
) -> None:
    client = app_for()
    pool_id = add_pool(client)

    listed = client.get("/pools").json()
    shown = client.get(f"/pools/{pool_id}").json()

    assert [pool["id"] for pool in listed] == [pool_id]
    assert (shown["name"], shown["cards"], shown["distinct"]) == ("Binder", 64, 5)
    assert {"name": ATRAXA, "color_identity": "WUBG"} in shown["commanders"]
    assert client.get(f"/pools/{uuid4()}").status_code == 404


def test_a_csv_pool_and_a_bad_request_are_handled(app_for: Callable[..., TestClient]) -> None:
    client = app_for()

    csv = client.post(
        "/pools",
        json={"name": "CSV", "format": "csv", "content": "Count,Name\n1,Sol Ring\n"},
    )
    empty_name = client.post("/pools", json={"name": "", "content": POOL_TEXT})

    assert csv.status_code == 201 and csv.json()["cards"] == 1
    assert empty_name.status_code == 422


# --- drafts and refines run in the background ------------------------------------


def test_a_draft_starts_at_once_and_its_run_is_polled_to_completion(
    app_for: Callable[..., TestClient],
    loaded: Settings,  # noqa: F811
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    pool_id = add_pool(client)

    started = draft(client, pool_id, request="Rats, please.")
    run = client.get(f"/runs/{started['run_id']}").json()

    assert started["status"] == "running"
    assert run["task"] == "draft" and run["status"] == "completed"
    assert run["deck_id"] == started["deck_id"] and run["pool_id"] == pool_id
    assert run["turns"] == 2 and run["final_text"] == "Drafted Atraxa and the rats."
    assert run["cost_usd"] > 0
    (proposal,) = run["proposals"]
    assert (proposal["kind"], proposal["status"]) == ("deck", "pending")


def test_a_run_keeps_the_trace_id_of_the_request_that_started_it(
    app_for: Callable[..., TestClient],
    loaded: Settings,  # noqa: F811
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    pool_id = add_pool(client)

    response = client.post(f"/pools/{pool_id}/drafts", json={})
    trace_id = response.headers["x-trace-id"]

    with connect(loaded) as conn:
        run_trace = conn.execute(
            "SELECT trace_id FROM agent_runs WHERE id = %s", (response.json()["run_id"],)
        ).fetchone()
    # The background run's model and tool calls log under the run's trace ID.
    assert run_trace == (trace_id,)


def test_a_refine_runs_against_the_saved_deck(
    app_for: Callable[..., TestClient],
    loaded: Settings,  # noqa: F811
) -> None:
    client = app_for(
        *DRAFT_SCRIPT,
        tool_call_reply(
            call(
                "propose_changes",
                add=[{"name": "Delver of Secrets"}],
                remove=[{"name": "Island"}],
                rationale="A flier.",
            ),
            model=SONNET,
        ),
        answer("Swapped an Island for Delver."),
    )
    started = draft(client, add_pool(client))
    (proposal,) = client.get(f"/runs/{started['run_id']}").json()["proposals"]
    with connect(loaded) as conn:  # the approval endpoints arrive in #100
        approve_proposal(conn, UUID(proposal["id"]))
        apply_proposal(conn, UUID(proposal["id"]))

    response = client.post(
        f"/decks/{started['deck_id']}/refinements", json={"request": "Add a flier."}
    )
    run = client.get(response.headers["location"]).json()

    assert response.status_code == 202
    assert (run["task"], run["status"]) == ("refine", "completed")
    assert run["proposals"][0]["kind"] == "changes"


def test_runs_need_something_to_run_on(app_for: Callable[..., TestClient]) -> None:
    client = app_for()
    pool_id = add_pool(client)
    deck_id = draft_without_running(client, pool_id)

    assert client.post(f"/pools/{uuid4()}/drafts", json={}).status_code == 404
    assert client.post(f"/decks/{uuid4()}/refinements", json={"request": "x"}).status_code == 404
    unsaved = client.post(f"/decks/{deck_id}/refinements", json={"request": "x"})
    assert unsaved.status_code == 409 and "no saved version" in unsaved.json()["detail"]
    assert client.post(f"/decks/{deck_id}/refinements", json={}).status_code == 422
    assert client.get(f"/runs/{uuid4()}").status_code == 404


def draft_without_running(client: TestClient, pool_id: str) -> str:
    """A deck with no saved version: a draft whose model fails at once."""
    started = draft(client, pool_id)
    return str(started["deck_id"])


def test_a_failed_run_is_reported_as_an_error(app_for: Callable[..., TestClient]) -> None:
    client = app_for(ModelCallError("the API is down"))

    started = draft(client, add_pool(client))
    run = client.get(f"/runs/{started['run_id']}").json()

    assert (run["status"], run["error"]) == ("error", "the API is down")


def test_runs_left_running_by_a_restart_are_marked_interrupted(
    loaded: Settings,  # noqa: F811
) -> None:
    with connect(loaded) as conn:
        row = conn.execute(
            "INSERT INTO agent_runs (task, model, status) VALUES ('rules', 'm', 'running') "
            "RETURNING id"
        ).fetchone()
    assert row is not None

    with TestClient(create_app(loaded, services=scripted())) as client:
        run = client.get(f"/runs/{row[0]}").json()

    assert run["status"] == "error"
    assert "interrupted" in run["error"]


# --- rules answers ------------------------------------------------------------------


def test_a_rules_question_is_answered_with_its_citations(
    app_for: Callable[..., TestClient],
) -> None:
    client = app_for(
        tool_call_reply(
            call("search_rules", question="color identity commander", k=3), model=SONNET
        ),
        answer("Cards must match the commander's colors.\nCitations: 903.4"),
    )

    response = client.post("/rules/answers", json={"question": "What is color identity?"})

    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert body["answer"] == "Cards must match the commander's colors."
    assert body["citations"][0]["number"] == "903.4"
    assert body["run_id"] and body["cost_usd"] > 0


def test_a_rules_question_can_come_back_not_found(app_for: Callable[..., TestClient]) -> None:
    client = app_for(answer("NOT FOUND. Nothing on proxies."))

    body = client.post("/rules/answers", json={"question": "Proxies?"}).json()

    assert (body["found"], body["answer"]) == (False, None)
    assert body["reason"] == "NOT FOUND. Nothing on proxies."
