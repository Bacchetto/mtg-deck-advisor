"""Evaluating rules answers: the real flow on a scripted model, then a scripted grader (#121)."""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

import json

import psycopg

from mtg_deck_advisor.evaluation.rules_qa import RulesCase, evaluate_case
from mtg_deck_advisor.evaluation.runner import Variant, variant_services
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.fake import FakeEmbedder, FakeProvider
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.types import ProviderResponse, Usage
from tests.integration.test_agent_flows import answer, call, calls, conn  # noqa: F401
from tests.integration.test_agent_tools import loaded  # noqa: F401

ANSWERABLE = RulesCase(
    id="R04",
    kind="commander",
    question="Does reminder text count toward color identity?",
    answerable=True,
    key_facts=["No: reminder text is ignored when determining color identity."],
    relevant=["903.4c"],
    also_acceptable=[],
    notes="",
)
UNANSWERABLE = RulesCase(
    id="N01",
    kind="not_found",
    question="Is it OK to use proxies in a casual Commander game?",
    answerable=False,
    key_facts=[],
    relevant=[],
    also_acceptable=[],
    notes="Not in the Comprehensive Rules.",
)


def grade(verdict: str, missing: list[str] | None = None) -> ProviderResponse:
    body = {
        "verdict": verdict,
        "missing_facts": missing or [],
        "contradictions": [],
        "reason": "Checked.",
    }
    return ProviderResponse(
        text=json.dumps(body),
        stop_reason="end_turn",
        usage=Usage(input_tokens=400, output_tokens=60),
        model="claude-sonnet-5-5",
    )


def evaluate(
    conn: psycopg.Connection, case: RulesCase, *agent: ProviderResponse, grader: FakeProvider
):
    variant = Variant(name="sonnet", model="claude-sonnet-5-5")
    services = variant_services(
        conn,
        variant,
        provider=FakeProvider(*agent),
        embedder=FakeEmbedder(),
        reranker=None,
        recorder=MemoryRecorder(),
        cost_cap_usd=1.0,
        max_turns=10,
    )
    grading = ModelClient(grader, "claude-sonnet-5-5", recorder=MemoryRecorder(), cost_cap_usd=1.0)
    return evaluate_case(services, grading, case)


def test_a_correct_answer_with_the_right_citation_succeeds(conn: psycopg.Connection) -> None:
    grader = FakeProvider(grade("correct"))

    result = evaluate(
        conn,
        ANSWERABLE,
        calls(call("search_rules", question="reminder text color identity", k=5)),
        answer("No, reminder text is ignored.\nCitations: 903.4c"),
        grader=grader,
    )

    assert result.success
    assert result.scores == {"correct": 1.0, "citation_hit": 1.0, "citation_precision": 1.0}
    assert result.details["cited"] == ["903.4c"] and result.details["verdict"] == "correct"
    assert result.grading_cost_usd > 0 and result.cost_usd > 0
    # The grader saw the key facts and the answer, not the citations line.
    (request,) = grader.calls
    prompt = request.messages[0].content
    assert "reminder text is ignored when determining color identity" in prompt
    assert "No, reminder text is ignored." in prompt and "Citations:" not in prompt


def test_a_partly_correct_answer_isnt_a_success(conn: psycopg.Connection) -> None:
    result = evaluate(
        conn,
        ANSWERABLE,
        calls(call("search_rules", question="reminder text", k=5)),
        answer("Probably not.\nCitations: 903.4c"),
        grader=FakeProvider(
            grade(
                "partly correct", ["No: reminder text is ignored when determining color identity."]
            )
        ),
    )

    assert not result.success and result.scores["correct"] == 0.5
    assert result.details["missing_facts"] == [
        "No: reminder text is ignored when determining color identity."
    ]


def test_saying_not_found_to_an_unanswerable_question_succeeds_without_grading(
    conn: psycopg.Connection,
) -> None:
    grader = FakeProvider()  # it must not be called

    result = evaluate(
        conn,
        UNANSWERABLE,
        calls(call("search_rules", question="proxies", k=5)),
        answer("NOT FOUND: the rules don't cover proxies."),
        grader=grader,
    )

    assert result.success and result.details["found"] is False
    assert grader.calls == [] and result.grading_cost_usd == 0


def test_answering_an_unanswerable_question_fails(conn: psycopg.Connection) -> None:
    result = evaluate(
        conn,
        UNANSWERABLE,
        calls(call("search_rules", question="proxies", k=5)),
        answer("Yes, proxies are fine.\nCitations: 903.4"),
        grader=FakeProvider(),
    )

    assert not result.success and result.details["found"] is True


def test_a_wrong_not_found_is_a_false_abstention(conn: psycopg.Connection) -> None:
    result = evaluate(
        conn,
        ANSWERABLE,
        calls(call("search_rules", question="reminder text", k=5)),
        answer("NOT FOUND: nothing about reminder text."),
        grader=FakeProvider(),
    )

    assert not result.success
    assert result.details["found"] is False and result.scores == {"correct": 0.0}
