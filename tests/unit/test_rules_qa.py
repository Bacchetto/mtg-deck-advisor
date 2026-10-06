"""The rules Q&A set and its deterministic scoring (#121, EVL-2)."""

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mtg_deck_advisor.evaluation.rules_qa import (
    RulesCase,
    citation_scores,
    load_cases,
    rules_metrics,
    rules_section,
)
from mtg_deck_advisor.evaluation.runner import CaseResult, EvalRun, Variant

SNAPSHOT_RULES = Path("evals/snapshot/rules.jsonl.gz")


def case(**overrides: object) -> RulesCase:
    fields: dict[str, object] = {
        "id": "R04",
        "kind": "commander",
        "question": "Does reminder text count toward color identity?",
        "answerable": True,
        "key_facts": ["No: reminder text is ignored."],
        "relevant": ["903.4c"],
        "also_acceptable": ["207.2"],
        "notes": "",
    }
    return RulesCase.model_validate(fields | overrides)


# --- the set -------------------------------------------------------------------------------


def test_the_set_has_the_dev_questions_and_unanswerable_ones() -> None:
    cases = load_cases()

    answerable = [c for c in cases if c.answerable]
    unanswerable = [c for c in cases if not c.answerable]
    assert len(answerable) == 45 and len(unanswerable) == 10
    assert len({c.id for c in cases}) == len(cases)
    assert all(c.key_facts and c.relevant for c in answerable)
    assert all(not c.key_facts and not c.relevant and c.notes for c in unanswerable)


def test_every_labelled_rule_exists() -> None:
    with gzip.open(SNAPSHOT_RULES, "rt", encoding="utf-8") as lines:
        numbers = {json.loads(line)["number"] for line in lines}

    labelled = {n for c in load_cases() for n in [*c.relevant, *c.also_acceptable]}

    assert labelled - numbers == set()


def test_an_answer_must_be_answerable_or_say_why_not() -> None:
    with pytest.raises(ValueError, match="key facts"):
        case(key_facts=[])
    with pytest.raises(ValueError, match="relevant"):
        case(relevant=[])


# --- citations -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cited", "hit", "precision"),
    [
        (["903.4c"], True, 1.0),  # the relevant rule
        (["903.4", "903.4c"], True, 1.0),  # its parent is fine too
        (["207.2"], False, 1.0),  # labelled also acceptable, but not the answering rule
        (["903.4c", "100.1"], True, 0.5),  # an unrelated rule
        (["903.4a"], False, 0.0),  # a sibling isn't the answer
        ([], False, None),
    ],
)
def test_citations_are_scored_against_the_labelled_rules(
    cited: list[str], hit: bool, precision: float | None
) -> None:
    assert citation_scores(case(), cited) == (hit, precision)


def test_a_rules_descendants_are_acceptable() -> None:
    family = case(relevant=["702.2"])

    assert citation_scores(family, ["702.2b", "702.2c"]) == (False, 1.0)


# --- suite metrics -------------------------------------------------------------------------


def result(case_id: str, *, answerable: bool, found: bool, **scores: float) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        status="completed",
        success=bool(scores.get("correct", 0) == 1) if answerable else not found,
        details={"answerable": answerable, "found": found},
        scores=scores,
    )


def test_suite_metrics_separate_correctness_citations_and_abstention() -> None:
    run = EvalRun(
        suite="rules",
        variant=Variant(name="sonnet", model="claude-sonnet-5-5"),
        started_at=datetime(2026, 10, 6, tzinfo=UTC),
        budget_usd=1.0,
        results=[
            result(
                "R01",
                answerable=True,
                found=True,
                correct=1.0,
                citation_hit=1.0,
                citation_precision=1.0,
            ),
            result(
                "R02",
                answerable=True,
                found=True,
                correct=0.5,
                citation_hit=0.0,
                citation_precision=0.5,
            ),
            result("R03", answerable=True, found=False),  # a wrong "not found"
            result("N01", answerable=False, found=False),  # a right one
            result("N02", answerable=False, found=True),  # answered the unanswerable
        ],
    )

    metrics = rules_metrics(run)

    assert metrics == {
        "correctness": pytest.approx(0.5),  # (1 + 0.5 + 0) over the 3 answerable
        "citation_hit": pytest.approx(0.5),  # of the 2 answered
        "citation_precision": pytest.approx(0.75),
        "abstention": pytest.approx(0.5),  # of the 2 unanswerable
        "false_abstention": pytest.approx(1 / 3),
    }
    assert "| sonnet | 50% | 50% | 75% | 50% | 33% |" in rules_section([run])
