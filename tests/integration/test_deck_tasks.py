"""Running deck-build tasks through the real flows, graded by a scripted grader (#122, EVL-4)."""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

import json
from pathlib import Path

import psycopg

from mtg_deck_advisor.evaluation.deck_tasks import CRITERIA, DeckTask, run_task
from mtg_deck_advisor.evaluation.runner import CaseResult, Variant, variant_services
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.fake import FakeEmbedder, FakeProvider
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.types import ProviderResponse, Usage
from tests.integration.test_agent_flows import answer, call, calls, conn  # noqa: F401
from tests.integration.test_agent_tools import (  # noqa: F401
    ATRAXA,
    LEGAL_CARDS,
    POOL,
    RATS,
    loaded,
)

LEGAL = {card["name"]: card["count"] for card in LEGAL_CARDS}


def pool_file(tmp_path: Path) -> Path:
    path = tmp_path / "pool.txt"
    path.write_text("".join(f"{n} {name}\n" for name, n in POOL.items()), encoding="utf-8")
    return path


def grade(**overrides: int | None) -> ProviderResponse:
    scores: dict[str, int | None] = {**dict.fromkeys(CRITERIA, 4), **overrides}
    body = {"scores": scores, "reasons": dict.fromkeys(CRITERIA, "Counted."), "summary": "Fine."}
    return ProviderResponse(
        text=json.dumps(body),
        stop_reason="end_turn",
        usage=Usage(input_tokens=3000, output_tokens=300),
        model="claude-opus-5-5",
    )


def task(**fields: object) -> DeckTask:
    base: dict[str, object] = {
        "id": "T01",
        "pool": "pool.txt",
        "kind": "draft",
        "request": "",
        "commander": None,
        "theme": "",
        "start": None,
        "notes": "",
    }
    return DeckTask.model_validate(base | fields)


def run(
    conn: psycopg.Connection,
    tmp_path: Path,
    deck_task: DeckTask,
    *agent: ProviderResponse,
    grader: FakeProvider,
) -> CaseResult:
    pool_file(tmp_path)
    services = variant_services(
        conn,
        Variant(name="sonnet", model="claude-sonnet-5-5"),
        provider=FakeProvider(*agent),
        embedder=FakeEmbedder(),
        reranker=None,
        recorder=MemoryRecorder(),
        cost_cap_usd=1.0,
        max_turns=10,
    )
    grading = ModelClient(grader, "claude-opus-5-5", recorder=MemoryRecorder(), cost_cap_usd=1.0)
    return run_task(services, grading, deck_task, datasets=tmp_path, pools={})


def test_a_draft_with_a_legal_proposal_succeeds_and_is_graded(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    grader = FakeProvider(grade(fit=None, ramp=2))

    result = run(
        conn,
        tmp_path,
        task(),
        calls(call("propose_deck", commander=ATRAXA, cards=LEGAL_CARDS, rationale="Rats.")),
        answer("Drafted."),
        grader=grader,
    )

    assert result.success and result.status == "completed"
    assert result.scores["ramp"] == 2.0 and "fit" not in result.scores
    assert result.scores["quality"] == (4 * 5 + 2) / 6
    assert result.details["commander"] == ATRAXA
    assert result.grading_cost_usd > 0
    # The grader saw each card's text and the counts, and no request.
    prompt = grader.calls[0].messages[0].content
    assert "Relentless Rats" in prompt and "A deck can have any number" in prompt
    assert "Lands:" in prompt and "Request: none" in prompt
    # The eval deck is archived, so it stays out of the user's lists.
    deck_id = result.details["deck_id"]
    row = conn.execute(
        "SELECT archived_at IS NOT NULL FROM decks WHERE id = %s", (deck_id,)
    ).fetchone()
    assert row is not None and row[0] is True


def test_a_draft_without_a_legal_proposal_fails_and_isnt_graded(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    grader = FakeProvider()  # must not be called

    result = run(
        conn,
        tmp_path,
        task(),
        calls(call("propose_deck", commander=ATRAXA, cards=LEGAL_CARDS[1:], rationale="Short.")),
        answer("I couldn't finish."),
        grader=grader,
    )

    assert not result.success and result.tools.rejected == 1
    assert grader.calls == [] and result.scores == {}


def test_a_named_commander_is_checked(conn: psycopg.Connection, tmp_path: Path) -> None:
    result = run(
        conn,
        tmp_path,
        task(id="T02", request="Build around Sol Ring.", commander="Sol Ring"),
        calls(call("propose_deck", commander=ATRAXA, cards=LEGAL_CARDS, rationale="Rats.")),
        answer("Drafted."),
        grader=FakeProvider(grade(fit=1)),
    )

    assert result.success  # legal, so it ran to a deck
    assert result.details["commander_match"] is False
    assert "Request: Build around Sol Ring." in result.details["deck_text"]


def test_a_refine_starts_from_the_saved_deck_and_grades_the_result(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    start = {"commander": ATRAXA, "cards": LEGAL}
    change = calls(
        call(
            "propose_changes",
            add=[{"name": "Delver of Secrets"}],
            remove=[{"name": "Island"}],
            rationale="More interaction.",
        )
    )

    result = run(
        conn,
        tmp_path,
        task(id="T09", kind="refine", request="Add removal.", start=start),
        change,
        answer("Swapped."),
        grader=FakeProvider(grade()),
    )

    assert result.success, result.details
    assert "Delver of Secrets" in result.details["deck_text"]
    assert result.details["version"] == 1  # the harness saved the start; nothing was applied
