"""The live CI eval: a few rules questions on the real model, gated by code (#124, EVL-5).

    python -m mtg_deck_advisor.evaluation.live_gate --yes                    # in CI
    python -m mtg_deck_advisor.evaluation.live_gate --prompt degraded --yes  # must fail

The free gate (`gate.py`) checks retrieval and the validator on every pull
request, but not the model or its prompt. This one asks the real agent the
eight rules questions in LIVE_CASES on pushes to main and on demand, scores the
answers by code alone (citations and "not found", no grader), and fails below
`evals/live_thresholds.toml`. A run costs a few cents and is capped.

It runs where the free gate does: in a throwaway database loaded from the
embedding snapshot. CI has no embedding model, so the model's own search
queries can't be embedded. Instead, during each case, a PinnedEmbedder answers
every search with the snapshot's vector for that case's question: a stand-in
that keeps the model, its prompt and its tool use real, while the search
results are those its question would find. With `--ollama` (and Ollama
running), searches are embedded for real.

`--prompt degraded` swaps in a system prompt without the citation and "not
found" instructions, to show the gate catches a worse prompt (#124's done-when).
"""

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import psycopg

from mtg_deck_advisor.agent.flows import AgentServices
from mtg_deck_advisor.agent.prompts import UNTRUSTED, system_prompt
from mtg_deck_advisor.agent.runs import Task
from mtg_deck_advisor.evaluation.gate import check, load_thresholds, report
from mtg_deck_advisor.evaluation.rules_qa import RulesCase, evaluate_case, load_cases, rules_metrics
from mtg_deck_advisor.evaluation.runner import (
    PROMPT_VARIANTS,
    CaseResult,
    EvalRun,
    Variant,
    confirm_spend,
    run_suite,
    variant_services,
)
from mtg_deck_advisor.evaluation.snapshot import SnapshotEmbedder
from mtg_deck_advisor.llm.ollama import Embedder
from mtg_deck_advisor.retrieval.text import query_text

LIVE_THRESHOLDS = Path("evals/live_thresholds.toml")
# Six answerable questions and two the rules don't answer, all passed by
# Sonnet 5.5 in the rules baseline (evals/reports/2026-10-06-rules-qa.md).
LIVE_CASES = ("R06", "R07", "R17", "R20", "R27", "R44", "N01", "N07")
MODEL = "claude-sonnet-5-5"
# About $0.005 per question in the rules baseline; the cap leaves room.
COST_PER_CASE = 0.008

# The rules prompt with its instructions to cite rules and say "NOT FOUND"
# taken out: the gate must fail on it.
DEGRADED_RULES = f"""\
You answer questions about the Magic: The Gathering rules. Use search_rules if \
it helps, and answer briefly.

{UNTRUSTED}"""


def degraded_prompt(task: Task) -> str:
    return DEGRADED_RULES if task == "rules" else system_prompt(task)


PROMPT_VARIANTS["degraded"] = degraded_prompt


class PinnedEmbedder:
    """The snapshot's query vectors, with any other query answered as the pinned question."""

    def __init__(self, snapshot: SnapshotEmbedder) -> None:
        self._snapshot = snapshot
        self._pinned: str | None = None

    @property
    def model(self) -> str:
        return self._snapshot.model

    def pin(self, question: str | None) -> None:
        """Answer unknown queries as this rules question, until pinned again."""
        self._pinned = None if question is None else query_text(self.model, "rules", question)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        known = set(self._snapshot.texts)
        wanted = [text if text in known or self._pinned is None else self._pinned for text in texts]
        return self._snapshot.embed(wanted)


def live_metrics(run: EvalRun) -> dict[str, float]:
    """Citations and "not found", by code, as live.rules.* metrics."""
    rules = rules_metrics(run)
    return {
        "live.rules.citation_hit": rules["citation_hit"],
        "live.rules.citation_precision": rules["citation_precision"],
        "live.rules.abstention": rules["abstention"],
        "live.rules.answered": 1.0 - rules["false_abstention"] - _withheld(run),
    }


def _withheld(run: EvalRun) -> float:
    """The share of answerable questions whose answer was withheld (not "NOT FOUND")."""
    answerable = [r for r in run.results if r.details.get("answerable")]
    withheld = [
        r for r in answerable if not r.details.get("found") and not r.details.get("abstained")
    ]
    return len(withheld) / len(answerable) if answerable else 0.0


def run_live_gate(
    conn: psycopg.Connection,
    services_for: Callable[[], AgentServices],
    cases: Sequence[RulesCase],
    variant: Variant,
    *,
    budget_usd: float,
    embedder: PinnedEmbedder | None = None,
) -> EvalRun:
    """Ask each question with fresh services, pinning searches to it, and score by code."""

    def evaluate(case: RulesCase) -> CaseResult:
        if embedder is not None:
            embedder.pin(case.question)
        result = evaluate_case(services_for(), None, case)
        conn.commit()
        return result

    return run_suite(
        "live_gate",
        cases,
        variant,
        evaluate,
        budget_usd=budget_usd,
        case_id=lambda case: case.id,
    )


def main(argv: Sequence[str] | None = None) -> int:
    from mtg_deck_advisor.config import get_settings
    from mtg_deck_advisor.db.connection import connect
    from mtg_deck_advisor.db.migrate import upgrade
    from mtg_deck_advisor.evaluation.gate import _database
    from mtg_deck_advisor.evaluation.injection import against
    from mtg_deck_advisor.evaluation.snapshot import load_snapshot
    from mtg_deck_advisor.llm.factory import build_embedder, build_provider
    from mtg_deck_advisor.llm.recording import DatabaseRecorder
    from mtg_deck_advisor.observability.logging import configure_logging

    parser = argparse.ArgumentParser(prog="python -m mtg_deck_advisor.evaluation.live_gate")
    parser.add_argument("--prompt", choices=["default", "degraded"], default="default")
    parser.add_argument("--ollama", action="store_true", help="embed searches with the real model")
    parser.add_argument("--budget", type=float, default=0.10, help="US dollars")
    parser.add_argument("--yes", action="store_true", help="don't ask before spending")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings)
    if settings.anthropic_api_key is None or not settings.anthropic_api_key.get_secret_value():
        print("ANTHROPIC_API_KEY isn't set (in CI: the repository secret).", file=sys.stderr)
        return 2
    by_id = {case.id: case for case in load_cases()}
    cases = [by_id[case_id] for case_id in LIVE_CASES]
    variant = Variant(name=f"sonnet-{args.prompt}", model=MODEL, prompt=args.prompt)
    if not confirm_spend(COST_PER_CASE * len(cases), budget_usd=args.budget, yes=args.yes):
        return 1

    with _database(None) as url:
        scratch = against(settings, url)
        upgrade(scratch)
        with connect(scratch) as conn:
            snapshot = load_snapshot(conn)
            conn.commit()
            pinned: PinnedEmbedder | None = None
            embedder: Embedder
            if args.ollama:
                embedder = build_embedder(scratch)
            else:
                pinned = embedder = PinnedEmbedder(snapshot)
            recorder = DatabaseRecorder(scratch)

            def services_for() -> AgentServices:
                return variant_services(
                    conn,
                    variant,
                    provider=build_provider(scratch),
                    embedder=embedder,
                    reranker=None,  # rules search doesn't rerank
                    recorder=recorder,
                    cost_cap_usd=scratch.agent_cost_cap_usd,
                    max_turns=scratch.agent_max_turns,
                )

            run = run_live_gate(
                conn, services_for, cases, variant, budget_usd=args.budget, embedder=pinned
            )

    metrics = live_metrics(run)
    thresholds = load_thresholds(LIVE_THRESHOLDS)
    failures = check(metrics, thresholds)
    notes = [
        f"{variant.name}: {len(cases)} rules questions, ${run.spent_usd:.4f}, "
        + ("searches embedded by the model" if args.ollama else "searches pinned to each question")
    ]
    for r in run.results:
        cited = ", ".join(r.details.get("cited", [])) or "nothing"
        notes.append(f"{r.case_id}: {'pass' if r.success else 'fail'} (cited {cited})")
    text = report(metrics, thresholds, notes + failures).replace("## Eval gate", "## Live eval")
    print(text)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as out:
            out.write(text)
    if failures:
        print(f"live eval FAILED: {len(failures)} metric(s) below threshold", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
