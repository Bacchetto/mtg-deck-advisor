"""The CLI's waiting and failure handling, against a mock API (#101)."""

import io
import json
from typing import Any

import httpx2

from mtg_deck_advisor.cli import main

RUN = "11111111-1111-1111-1111-111111111111"
DECK = "22222222-2222-2222-2222-222222222222"


def run_view(status: str, turns: int, cost: float) -> dict[str, Any]:
    return {
        "id": RUN,
        "task": "draft",
        "status": status,
        "pool_id": None,
        "deck_id": DECK,
        "model": "claude-sonnet-5-5",
        "turns": turns,
        "cost_usd": cost,
        "final_text": "Done." if status != "running" else None,
        "error": None,
        "started_at": "2026-10-05T00:00:00Z",
        "finished_at": None,
        "proposals": [],
    }


class Api:
    """A mock API: a draft that runs for two polls before completing."""

    def __init__(self) -> None:
        self.polls = [run_view("running", 1, 0.01), run_view("running", 3, 0.04)]
        self.requests: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            started = {"run_id": RUN, "deck_id": DECK, "status": "running"}
            return httpx2.Response(202, json=started)
        body = self.polls.pop(0) if self.polls else run_view("completed", 4, 0.05)
        return httpx2.Response(200, json=body)


def cli(api: Any, *args: str) -> tuple[int, str, str, list[float]]:
    out, err, sleeps = io.StringIO(), io.StringIO(), []
    http = httpx2.Client(transport=httpx2.MockTransport(api), base_url="http://api.test")
    code = main(list(args), http=http, out=out, err=err, sleep=sleeps.append)
    return code, out.getvalue(), err.getvalue(), sleeps


def test_a_draft_waits_for_its_run_and_shows_progress() -> None:
    api = Api()

    code, out, _, sleeps = cli(api, "draft", "pool-1")

    assert code == 0
    assert api.requests == ["POST /pools/pool-1/drafts"] + [f"GET /runs/{RUN}"] * 3
    assert len(sleeps) == 3
    assert "turn 1, $0.0100" in out and "turn 3, $0.0400" in out
    assert "completed after 4 turns, $0.0500" in out


def test_no_wait_returns_the_run_at_once() -> None:
    api = Api()

    code, out, _, sleeps = cli(api, "--json", "draft", "pool-1", "--no-wait")

    assert code == 0 and sleeps == []
    assert json.loads(out) == {"run_id": RUN, "deck_id": DECK, "status": "running"}


def test_an_unreachable_api_is_reported_plainly() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    code, out, err, _ = cli(refuse, "pool", "list")

    assert code == 1 and out == ""
    assert "cannot reach the API" in err and "docker compose up" in err


def test_usage_errors_exit_with_code_2() -> None:
    code, _, _, _ = cli(Api(), "approve")  # no proposal ID

    assert code == 2
