"""Running deck-build tasks through the real flows, graded by a scripted grader (#122, EVL-4)."""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

import json
from pathlib import Path

import psycopg

from mtg_deck_advisor.agent.flows import AgentServices
from mtg_deck_advisor.evaluation.deck_tasks import CRITERIA, DeckTask, make_evaluator, run_task
from mtg_deck_advisor.evaluation.runner import CaseResult, Variant, run_suite, variant_services
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
    assert result.scores["quality"] == (4 * 4 + 2) / 5  # fit is skipped: five criteria
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


def test_each_task_gets_its_own_agent_budget(conn: psycopg.Connection, tmp_path: Path) -> None:
    # The cost cap is per run. Sharing one client across a suite made later
    # tasks stop at "budget" once earlier ones had spent the cap between them.
    pool_file(tmp_path)
    made: list[AgentServices] = []

    def services_for() -> AgentServices:
        provider = FakeProvider(
            calls(call("propose_deck", commander=ATRAXA, cards=LEGAL_CARDS, rationale="Rats.")),
            answer("Drafted."),
        )
        made.append(
            variant_services(
                conn,
                Variant(name="sonnet", model="claude-sonnet-5-5"),
                provider=provider,
                embedder=FakeEmbedder(),
                reranker=None,
                recorder=MemoryRecorder(),
                cost_cap_usd=1.0,
                max_turns=10,
            )
        )
        return made[-1]

    grading = ModelClient(
        FakeProvider(grade(fit=None), grade(fit=None)),
        "claude-opus-5-5",
        recorder=MemoryRecorder(),
        cost_cap_usd=1.0,
    )
    evaluate = make_evaluator(conn, services_for, grading, datasets=tmp_path)

    run = run_suite(
        "deck_tasks",
        [task(id="T01"), task(id="T02")],
        Variant(name="sonnet", model="claude-sonnet-5-5"),
        evaluate,
        budget_usd=1.0,
        case_id=lambda t: t.id,
    )

    assert [r.success for r in run.results] == [True, True]
    assert len(made) == 2 and made[0].client is not made[1].client
    assert made[1].client.spent_usd == run.results[1].cost_usd  # only its own task


# --- grading a refine on its change (#136) -----------------------------------------------


def refined(conn: psycopg.Connection, tmp_path: Path, grader: FakeProvider) -> CaseResult:
    start = {"commander": ATRAXA, "cards": LEGAL}
    change = calls(
        call(
            "propose_changes",
            add=[{"name": "Delver of Secrets"}],
            remove=[{"name": "Island"}],
            rationale="A cheap flier.",
        )
    )
    return run(
        conn,
        tmp_path,
        task(id="T09", kind="refine", request="Add a cheap flier.", start=start),
        change,
        answer("Swapped."),
        grader=grader,
    )


def test_a_refines_grader_sees_the_starting_deck_and_the_change(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    # The grader saw only the final deck, so it couldn't tell what a refine
    # changed: "without the original list it can't be confirmed" (T13).
    result = refined(conn, tmp_path, FakeProvider(grade()))

    text = result.details["deck_text"]
    assert "The change from the starting deck" in text
    assert "Lands: 37 → 36" in text
    assert "Cut: 1 Island" in text
    assert "Added: 1 Delver of Secrets" in text


def test_a_drafts_grader_text_has_no_change_section(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    result = run(
        conn,
        tmp_path,
        task(),
        calls(call("propose_deck", commander=ATRAXA, cards=LEGAL_CARDS, rationale="r")),
        answer("Drafted."),
        grader=FakeProvider(grade()),
    )

    assert "The change from the starting deck" not in result.details["deck_text"]


def test_regrading_a_saved_run_grades_its_refines_on_their_change(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    from datetime import UTC, datetime

    from mtg_deck_advisor.evaluation.deck_tasks import regrade
    from mtg_deck_advisor.evaluation.runner import EvalRun

    first = refined(conn, tmp_path, FakeProvider(grade(fit=2)))
    # A run saved before the change section existed: its text has none.
    first.details["deck_text"] = first.details["deck_text"].split("\n\nThe change from")[0]
    draft = CaseResult(case_id="T01", status="completed", success=True, scores={"fit": 3.0})
    saved = EvalRun(
        suite="deck_tasks",
        variant=Variant(name="sonnet", model="claude-sonnet-5-5"),
        started_at=datetime(2026, 10, 8, tzinfo=UTC),
        budget_usd=1.0,
        results=[draft, first],
    )
    grader = FakeProvider(grade(fit=5))
    grading = ModelClient(grader, "claude-opus-5-5", recorder=MemoryRecorder(), cost_cap_usd=1.0)
    start = {"commander": ATRAXA, "cards": LEGAL}
    tasks = [task(id="T09", kind="refine", request="Add a cheap flier.", start=start)]

    regraded = regrade(conn, grading, saved, tasks)

    refine = next(r for r in regraded.results if r.case_id == "T09")
    assert refine.scores["fit"] == 5.0
    assert "Cut: 1 Island" in refine.details["deck_text"]
    assert refine.grading_cost_usd > 0
    assert next(r for r in regraded.results if r.case_id == "T01").scores == {"fit": 3.0}
    assert regraded.variant.name == "sonnet-regraded"
    assert len(grader.calls) == 1  # only the refine was graded again
