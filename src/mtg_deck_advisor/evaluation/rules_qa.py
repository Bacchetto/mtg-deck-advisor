"""The rules Q&A eval: correctness, citation accuracy and "not found" (#121, EVL-2, EVL-3).

    python -m mtg_deck_advisor.evaluation.rules_qa --variant sonnet   # asks before spending
    python -m mtg_deck_advisor.evaluation.rules_qa --variant sonnet --variant haiku --yes

Each question in `evals/datasets/rules_qa.jsonl` goes through the real rules
flow (`answer_rules_question`: search, answer, the citation guardrail), and
the result is scored three ways:

- **Correctness**, graded by a model against the question's key facts with
  the written rubric `evals/rubrics/rules_answer.md`: correct (1), partly
  correct (0.5) or incorrect (0). A sample is graded by the owner too, and
  the two are compared (EVL-3).
- **Citations**, checked by code: the answer must cite at least one of the
  question's relevant rules (a "hit"), and each rule it cites must be
  acceptable (precision): a relevant rule, its parent, a rule below it, a
  lettered sub-rule beside it, or one labelled `also_acceptable` (a rule the
  relevant one cross-references). A saved run can be rescored against
  reviewed labels without calling a model again (`--rescore`).
- **"Not found"**: the 10 questions the Comprehensive Rules don't answer
  must get "NOT FOUND". Answering them, or saying "not found" to an
  answerable question, are both measured.

A case succeeds when an answerable question gets a correct answer that cites
an answering rule and nothing unacceptable, or an unanswerable one gets "NOT
FOUND".
"""

import argparse
import statistics
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Literal, Self

import psycopg
from pydantic import BaseModel, model_validator

from mtg_deck_advisor.agent.flows import AgentServices, answer_rules_question
from mtg_deck_advisor.evaluation.runner import (
    CaseResult,
    EvalRun,
    Variant,
    comparison_report,
    confirm_spend,
    result_from_run,
    run_suite,
    save_report,
    variant_services,
)
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.errors import InvalidOutputError
from mtg_deck_advisor.llm.types import Message, ModelRequest

RULES_QA = Path("evals/datasets/rules_qa.jsonl")
RUBRIC = Path("evals/rubrics/rules_answer.md")
GRADER_MODEL = "claude-sonnet-5-5"

VARIANTS = {
    "sonnet": Variant(name="sonnet", model="claude-sonnet-5-5"),
    "haiku": Variant(name="haiku", model="claude-haiku-4-5"),
    "sonnet-hybrid-rules": Variant(
        name="sonnet-hybrid-rules", model="claude-sonnet-5-5", rules_search="hybrid"
    ),
}
# Expected cost per question, from the recorded live runs (the agent's answer
# took $0.003-0.0065 on Sonnet 5.5), with room to spare; grading is one short call.
AGENT_COST_PER_CASE = {"claude-sonnet-5-5": 0.008, "claude-haiku-4-5": 0.004}
GRADING_COST_PER_CASE = 0.004


class RulesCase(BaseModel):
    id: str
    kind: str
    question: str
    answerable: bool
    # What a correct answer must state; empty for an unanswerable question.
    key_facts: list[str]
    # Rules that answer it.
    relevant: list[str]
    # Other rules fine to cite, such as one the answering rule refers to.
    also_acceptable: list[str]
    notes: str

    @model_validator(mode="after")
    def _labelled(self) -> Self:
        if self.answerable and not self.key_facts:
            raise ValueError(f"{self.id}: an answerable question needs key facts")
        if self.answerable and not self.relevant:
            raise ValueError(f"{self.id}: an answerable question needs relevant rules")
        return self


def load_cases(path: Path = RULES_QA) -> list[RulesCase]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [RulesCase.model_validate_json(line) for line in lines if line.strip()]


# --- citations -----------------------------------------------------------------------------


def _parent(number: str) -> str | None:
    """903.5a's parent is 903.5; 903.5's is 903; 903 has none."""
    if number[-1].isalpha():
        return number.rstrip("abcdefghijklmnopqrstuvwxyz")
    if "." in number:
        return number.split(".", 1)[0]
    return None


def _in_family(number: str, rule: str) -> bool:
    """`number` is `rule`, its parent, a rule below it, or a lettered sub-rule beside it.

    Siblings count only among lettered sub-rules (702.124d beside 702.124a,
    both about partner). Numbered rules beside each other can be about other
    things entirely: 903.12 (Brawl) sits beside 903.5 (Commander's deck rules).
    """
    if number in (rule, _parent(rule)):
        return True
    if number[-1].isalpha() and rule[-1].isalpha() and _parent(number) == _parent(rule):
        return True
    below = number[len(rule) :] if number.startswith(rule) else ""
    return bool(below) and (below[0].isalpha() or below[0] == ".")


def citation_scores(case: RulesCase, cited: Sequence[str]) -> tuple[bool, float | None]:
    """Whether an answering rule is cited, and the share of citations that are acceptable."""
    if not cited:
        return False, None
    hit = any(number in case.relevant for number in cited)
    acceptable = [
        number
        for number in cited
        if number in case.also_acceptable or any(_in_family(number, r) for r in case.relevant)
    ]
    return hit, len(acceptable) / len(cited)


# --- grading -------------------------------------------------------------------------------

Verdict = Literal["correct", "partly correct", "incorrect"]
VERDICT_SCORES: dict[str, float] = {"correct": 1.0, "partly correct": 0.5, "incorrect": 0.0}


class Grade(BaseModel):
    verdict: Verdict
    missing_facts: list[str]
    contradictions: list[str]
    reason: str


def grade_answer(grader: ModelClient, case: RulesCase, answer: str) -> Grade:
    """The grader's verdict on one answer, by the rubric."""
    facts = "\n".join(f"{n}. {fact}" for n, fact in enumerate(case.key_facts, 1))
    request = ModelRequest(
        purpose="rules_grading",
        system=(
            "You grade answers to Magic: The Gathering rules questions for correctness, "
            "following this rubric exactly. Keep the reason to one or two sentences.\n\n"
            + RUBRIC.read_text(encoding="utf-8")
        ),
        messages=(
            Message(
                role="user",
                content=(
                    f"Question:\n{case.question}\n\nKey facts:\n{facts}\n\n"
                    f"Answer to grade:\n<answer>\n{answer}\n</answer>"
                ),
            ),
        ),
        # Room for a full grade; 600 cut some off mid-JSON in the first baseline.
        max_tokens=1500,
    )
    return grader.generate_structured(request, Grade).value


def evaluate_case(services: AgentServices, grader: ModelClient, case: RulesCase) -> CaseResult:
    """Ask the question through the real flow, then score the answer."""
    answered = answer_rules_question(services, case.question)
    cited = [rule.number for rule in answered.citations]
    abstained = not answered.found and answered.reason.upper().startswith("NOT FOUND")
    details: dict[str, object] = {
        "question": case.question,
        "answerable": case.answerable,
        "found": answered.found,
        "abstained": abstained,
        "answer": answered.answer,
        "reason": answered.reason,
        "cited": cited,
    }
    scores: dict[str, float] = {}
    graded_before = grader.spent_usd
    success = False
    if case.answerable and answered.found and answered.answer is not None:
        hit, precision = citation_scores(case, cited)
        scores = {
            "citation_hit": float(hit),
            "citation_precision": precision if precision is not None else 0.0,
        }
        try:
            grade = grade_answer(grader, case, answered.answer)
        except InvalidOutputError as exc:
            # Kept as an ungraded case, with its costs, rather than lost.
            details["grading_error"] = str(exc)
        else:
            scores["correct"] = VERDICT_SCORES[grade.verdict]
            details |= {
                "verdict": grade.verdict,
                "missing_facts": grade.missing_facts,
                "contradictions": grade.contradictions,
                "grade_reason": grade.reason,
            }
            success = grade.verdict == "correct" and hit and precision == 1.0
    elif case.answerable:
        scores = {"correct": 0.0}
    else:
        success = abstained
    result = result_from_run(
        services.conn, answered.run.run_id, case.id, success=success, details=details
    )
    result.scores = scores
    result.grading_cost_usd = grader.spent_usd - graded_before
    return result


def rescore(run: EvalRun, cases: Sequence[RulesCase]) -> EvalRun:
    """The run scored again against the current labels, by code alone; the run is unchanged.

    Citation hits, precision and success are recomputed from each saved
    answer's citations and grade. Nothing is asked of a model, so it's free.
    """
    by_id = {case.id: case for case in cases}
    rescored = run.model_copy(deep=True)
    for result in rescored.results:
        case = by_id.get(result.case_id)
        if case is None or not case.answerable or not result.details.get("found"):
            continue
        hit, precision = citation_scores(case, result.details.get("cited", []))
        result.scores["citation_hit"] = float(hit)
        result.scores["citation_precision"] = precision if precision is not None else 0.0
        result.success = result.details.get("verdict") == "correct" and hit and precision == 1.0
    return rescored


# --- metrics and reports -------------------------------------------------------------------


def _mean(values: Sequence[float]) -> float:
    return statistics.mean(values) if values else 0.0


def rules_metrics(run: EvalRun) -> dict[str, float]:
    """Correctness, citations and abstention, kept apart (EVL-2)."""
    ran = [r for r in run.results if r.status not in ("skipped", "error")]
    answerable = [r for r in ran if r.details.get("answerable")]
    unanswerable = [r for r in ran if not r.details.get("answerable")]
    answered = [r for r in answerable if r.details.get("found")]

    def abstained(r: CaseResult) -> bool:
        return bool(r.details.get("abstained", not r.details.get("found")))

    return {
        "correctness": _mean(
            [r.scores.get("correct", 0.0) for r in answerable if "grading_error" not in r.details]
        ),
        "citation_hit": _mean([r.scores.get("citation_hit", 0.0) for r in answered]),
        "citation_precision": _mean([r.scores.get("citation_precision", 0.0) for r in answered]),
        "abstention": _mean([float(abstained(r)) for r in unanswerable]),
        "false_abstention": _mean([float(abstained(r)) for r in answerable]),
    }


def rules_section(runs: Sequence[EvalRun]) -> str:
    lines = [
        "## Rules Q&A metrics",
        "",
        "- **Correct:** mean rubric score over the answerable questions (correct 1, partly 0.5).",
        "- **Cites an answering rule / citation precision:** over the answers given.",
        '- **"Not found" when it should:** over the unanswerable; **wrongly:** the answerable.',
        "",
        '| Variant | Correct | Cites an answering rule | Citation precision | "Not found" when it '
        'should | "Not found" wrongly |',
        "|---|---|---|---|---|---|",
    ]
    for run in runs:
        m = rules_metrics(run)
        lines.append(
            f"| {run.variant.name} | {m['correctness']:.0%} | {m['citation_hit']:.0%} | "
            f"{m['citation_precision']:.0%} | {m['abstention']:.0%} | {m['false_abstention']:.0%} |"
        )
    return "\n".join(lines) + "\n"


def estimate_usd(variants: Sequence[Variant], cases: int) -> float:
    return sum(
        cases * (AGENT_COST_PER_CASE.get(v.model, 0.016) + GRADING_COST_PER_CASE) for v in variants
    )


def main(argv: Sequence[str] | None = None) -> int:
    from mtg_deck_advisor.config import get_settings
    from mtg_deck_advisor.db.connection import connect
    from mtg_deck_advisor.llm.factory import build_embedder, build_provider
    from mtg_deck_advisor.llm.recording import DatabaseRecorder
    from mtg_deck_advisor.observability.logging import configure_logging

    parser = argparse.ArgumentParser(prog="python -m mtg_deck_advisor.evaluation.rules_qa")
    parser.add_argument("--variant", action="append", choices=list(VARIANTS), default=[])
    parser.add_argument("--ids", nargs="*", help="only these question IDs")
    parser.add_argument("--budget", type=float, default=1.0, help="US dollars, for all variants")
    parser.add_argument("--yes", action="store_true", help="don't ask before spending")
    parser.add_argument(
        "--rescore", type=Path, help="score a saved run again against the current labels (free)"
    )
    parser.add_argument(
        "--rerun-errors",
        action="store_true",
        help="with --rescore: run again the cases that errored or couldn't be graded (paid)",
    )
    args = parser.parse_args(argv)
    if not args.variant and not args.rescore:
        parser.error("give --variant to run, or --rescore RUN.json")

    settings = get_settings()
    configure_logging(settings)
    cases = [c for c in load_cases() if not args.ids or c.id in args.ids]
    saved: EvalRun | None = EvalRun.load(args.rescore) if args.rescore else None
    if saved is not None:
        variants = [saved.variant]
        retry = {
            r.case_id
            for r in saved.results
            if args.rerun_errors and (r.status == "error" or "grading_error" in r.details)
        }
        todo = [c for c in cases if c.id in retry]
    else:
        variants = [VARIANTS[name] for name in args.variant]
        todo = cases
    if todo:
        estimate = estimate_usd(variants, len(todo))
        print(f"{len(todo)} questions x {len(variants)} variant(s), graded by {GRADER_MODEL}.")
        if not confirm_spend(estimate, budget_usd=args.budget, yes=args.yes):
            return 1

    runs: list[EvalRun] = []
    per_variant = args.budget / len(variants)
    if todo:
        with connect(settings) as conn:
            embedder = build_embedder(settings)
            recorder = DatabaseRecorder(settings)
            grader = ModelClient(
                build_provider(settings), GRADER_MODEL, recorder=recorder, cost_cap_usd=args.budget
            )
            for variant in variants:
                services = variant_services(
                    conn,
                    variant,
                    provider=build_provider(settings),
                    embedder=embedder,
                    reranker=None,  # rules search doesn't rerank
                    recorder=recorder,
                    cost_cap_usd=settings.agent_cost_cap_usd,
                    max_turns=settings.agent_max_turns,
                )
                runs.append(
                    run_suite(
                        "rules_qa",
                        todo,
                        variant,
                        _evaluator(conn, services, grader),
                        budget_usd=per_variant,
                        case_id=lambda case: case.id,
                    )
                )

    notes = (
        f"Set: `{RULES_QA.as_posix()}` ({len(cases)} questions). Correctness graded by "
        f"{GRADER_MODEL} with [the rubric](../rubrics/{RUBRIC.name}). Citations and "
        '"not found" are checked by code.'
    )
    if saved is not None and args.rescore is not None:
        merged = saved
        if runs:
            again = {r.case_id: r for r in runs[0].results}
            merged = saved.model_copy(
                update={"results": [again.get(r.case_id, r) for r in saved.results]}
            )
        rescored = rescore(merged, load_cases())
        rescored.save(args.rescore.parent.parent)  # back to evals/runs/rules_qa/<same name>
        runs = [rescored]
        notes += f" Rescored on {date.today().isoformat()} against the labels as reviewed" + (
            f"; run again: {', '.join(sorted(again))}." if todo else "."
        )
    else:
        for run in runs:
            print(f"{run.variant.name}: saved {run.save()}, spent ${run.spent_usd:.4f}")

    report = comparison_report("Rules Q&A", runs, notes=notes) + "\n" + rules_section(runs)
    path = save_report(report, "rules-qa")
    print(report)
    print(f"saved {path}")
    return 0


def _evaluator(
    conn: psycopg.Connection, services: AgentServices, grader: ModelClient
) -> Callable[[RulesCase], CaseResult]:
    """Evaluate a case, then commit, so its run records are kept whatever happens next."""

    def evaluate(case: RulesCase) -> CaseResult:
        result = evaluate_case(services, grader, case)
        conn.commit()
        return result

    return evaluate


if __name__ == "__main__":
    sys.exit(main())
