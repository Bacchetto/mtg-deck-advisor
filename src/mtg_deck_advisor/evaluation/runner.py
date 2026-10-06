"""Running eval cases against variants, and reporting them side by side (#120, EVL-4, EVL-6).

A **variant** is one way of running the agent: a model, a system prompt, and
how its tools search. A **suite** (rules questions, deck tasks, injection
cases) runs each of its cases once per variant through the real flows, and
records per case (EVL-4):

- whether it succeeded, by the suite's own definition
- cost, turns and tool-call outcomes (ok, error, rejected), from the run's
  own records in the database
- wall-clock latency

A run is saved as JSON under `evals/runs/<suite>/`, named by when it started
and the variant, so nothing is overwritten. Runs are compared in a dated
markdown report under `evals/reports/` (EVL-6), with a summary table per
variant and a table per case.

Spending is explicit. A suite estimates its cost first, and
`confirm_spend` asks before any paid call unless `--yes` was given (and
refuses an estimate over the budget outright). During the run, cases are
skipped, not run, once the budget is spent.
"""

import io
import shutil
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO
from uuid import UUID

import psycopg
from pydantic import BaseModel

from mtg_deck_advisor.agent.flows import AgentServices
from mtg_deck_advisor.agent.prompts import system_prompt
from mtg_deck_advisor.agent.runs import Task
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.ollama import Embedder
from mtg_deck_advisor.llm.recording import CallRecorder
from mtg_deck_advisor.llm.types import Provider
from mtg_deck_advisor.retrieval.rerank import Reranker
from mtg_deck_advisor.retrieval.search import SearchMode

RUNS = Path("evals/runs")
REPORTS = Path("evals/reports")

# System prompts a variant can name. "default" is what ships; others are
# registered by the evals that compare against them.
PROMPT_VARIANTS: dict[str, Callable[[Task], str]] = {"default": system_prompt}


class Variant(BaseModel):
    name: str
    model: str
    # A key of PROMPT_VARIANTS.
    prompt: str = "default"
    card_search: SearchMode = "hybrid"
    # Rerank card searches with the local reranker (when one is configured).
    rerank: bool = True
    rules_search: SearchMode = "vector"


class ToolCounts(BaseModel):
    ok: int = 0
    error: int = 0
    rejected: int = 0


class CaseResult(BaseModel):
    case_id: str
    # The run's status ("completed", "cost_capped"...), or "skipped" (over
    # budget) or "error" (the case itself failed).
    status: str
    success: bool
    cost_usd: float = 0.0
    latency_s: float = 0.0
    turns: int = 0
    tools: ToolCounts = ToolCounts()
    run_id: UUID | None = None
    # What the suite wants to keep: the answer, the proposal, the citations...
    details: dict[str, Any] = {}
    # Grades added later (rubric scores, hand checks).
    scores: dict[str, float] = {}
    error: str | None = None


class EvalRun(BaseModel):
    suite: str
    variant: Variant
    started_at: datetime
    finished_at: datetime | None = None
    # The commit the code was at, so a result can be traced to its code.
    commit: str | None = None
    budget_usd: float
    results: list[CaseResult] = []

    @property
    def total_cost_usd(self) -> float:
        return sum(result.cost_usd for result in self.results)

    def save(self, directory: Path = RUNS) -> Path:
        stamp = self.started_at.astimezone(UTC).strftime("%Y-%m-%dT%H%M%S")
        path = directory / self.suite / f"{stamp}-{self.variant.name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "EvalRun":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def variant_services(
    conn: psycopg.Connection,
    variant: Variant,
    *,
    provider: Provider,
    embedder: Embedder,
    reranker: Reranker[UUID] | None,
    recorder: CallRecorder,
    cost_cap_usd: float,
    max_turns: int,
) -> AgentServices:
    """The agent's services set up as the variant says."""
    client = ModelClient(provider, variant.model, recorder=recorder, cost_cap_usd=cost_cap_usd)
    return AgentServices(
        conn=conn,
        client=client,
        embedder=embedder,
        reranker=reranker if variant.rerank else None,
        max_turns=max_turns,
        system=PROMPT_VARIANTS[variant.prompt],
        card_search_mode=variant.card_search,
        rules_search_mode=variant.rules_search,
    )


def result_from_run(
    conn: psycopg.Connection,
    run_id: UUID,
    case_id: str,
    *,
    success: bool,
    details: Mapping[str, Any] | None = None,
) -> CaseResult:
    """A case's result, with its cost, turns and tool outcomes read from the run's records."""
    row = conn.execute(
        "SELECT status, turns, cost_usd::float8 FROM agent_runs WHERE id = %s", (run_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"there is no run {run_id}")
    outcomes: dict[str, int] = dict(
        conn.execute(
            "SELECT outcome, count(*)::int FROM tool_calls WHERE run_id = %s GROUP BY outcome",
            (run_id,),
        ).fetchall()
    )
    return CaseResult(
        case_id=case_id,
        status=row[0],
        success=success,
        cost_usd=row[2],
        turns=row[1],
        tools=ToolCounts(**outcomes),
        run_id=run_id,
        details=dict(details or {}),
    )


def _commit() -> str | None:
    """The short hash of the checked-out commit, or None outside a git checkout."""
    git = shutil.which("git")
    if git is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603 (fixed arguments)
            [git, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def run_suite[C](
    suite: str,
    cases: Sequence[C],
    variant: Variant,
    execute: Callable[[C], CaseResult],
    *,
    budget_usd: float,
    case_id: Callable[[C], str] = str,
) -> EvalRun:
    """Run every case once for the variant, skipping the rest once the budget is spent."""
    run = EvalRun(
        suite=suite,
        variant=variant,
        started_at=datetime.now(UTC),
        commit=_commit(),
        budget_usd=budget_usd,
    )
    for case in cases:
        if run.total_cost_usd >= budget_usd:
            run.results.append(CaseResult(case_id=case_id(case), status="skipped", success=False))
            continue
        started = time.perf_counter()
        try:
            result = execute(case)
        except Exception as exc:
            result = CaseResult(
                case_id=case_id(case), status="error", success=False, error=repr(exc)
            )
        result.latency_s = time.perf_counter() - started
        run.results.append(result)
    run.finished_at = datetime.now(UTC)
    return run


class Summary(BaseModel):
    variant: str
    model: str
    cases: int
    ran: int
    skipped: int
    # Of the cases that ran.
    success_rate: float
    total_cost_usd: float
    mean_cost_usd: float
    median_latency_s: float
    mean_turns: float
    tool_errors: int
    tool_rejections: int


def summarize(run: EvalRun) -> Summary:
    ran = [r for r in run.results if r.status != "skipped"]
    return Summary(
        variant=run.variant.name,
        model=run.variant.model,
        cases=len(run.results),
        ran=len(ran),
        skipped=len(run.results) - len(ran),
        success_rate=sum(r.success for r in ran) / len(ran) if ran else 0.0,
        total_cost_usd=round(run.total_cost_usd, 6),
        mean_cost_usd=round(statistics.mean(r.cost_usd for r in ran), 6) if ran else 0.0,
        median_latency_s=statistics.median(r.latency_s for r in ran) if ran else 0.0,
        mean_turns=statistics.mean(r.turns for r in ran) if ran else 0.0,
        tool_errors=sum(r.tools.error for r in ran),
        tool_rejections=sum(r.tools.rejected for r in ran),
    )


def _outcome(result: CaseResult | None) -> str:
    if result is None:
        return "-"
    if result.status in ("skipped", "error"):
        return result.status
    return "pass" if result.success else "fail"


def comparison_report(
    title: str, runs: Sequence[EvalRun], *, now: datetime | None = None, notes: str = ""
) -> str:
    """A markdown report: one summary row per variant, then each case across the variants."""
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    out = io.StringIO()
    out.write(f"# {title}, {stamp}\n\n")
    if notes:
        out.write(notes.rstrip() + "\n\n")
    out.write("## Variants\n\n")
    out.write(
        "| Variant | Model | Prompt | Card search | Rules search | Rerank | Commit |\n"
        "|---|---|---|---|---|---|---|\n"
    )
    for run in runs:
        v = run.variant
        out.write(
            f"| {v.name} | {v.model} | {v.prompt} | {v.card_search} | {v.rules_search} | "
            f"{'yes' if v.rerank else 'no'} | {run.commit or '-'} |\n"
        )
    out.write("\n## Results\n\n")
    out.write(
        "| Variant | Cases | Success | Total cost | Mean cost | Median latency | Mean turns "
        "| Tool errors | Rejected proposals | Skipped |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
    )
    for s in map(summarize, runs):
        out.write(
            f"| {s.variant} | {s.ran} | {s.success_rate:.0%} | ${s.total_cost_usd:.4f} | "
            f"${s.mean_cost_usd:.4f} | {s.median_latency_s:.1f} s | {s.mean_turns:.1f} | "
            f"{s.tool_errors} | {s.tool_rejections} | {s.skipped} |\n"
        )
    out.write("\n## Per case\n\n")
    out.write("| Case | " + " | ".join(run.variant.name for run in runs) + " |\n")
    out.write("|---|" + "---|" * len(runs) + "\n")
    case_ids = list(dict.fromkeys(r.case_id for run in runs for r in run.results))
    by_run = [{r.case_id: r for r in run.results} for run in runs]
    for case in case_ids:
        out.write(f"| {case} | " + " | ".join(_outcome(rows.get(case)) for rows in by_run) + " |\n")
    return out.getvalue()


def save_report(
    text: str, suite: str, *, directory: Path = REPORTS, now: datetime | None = None
) -> Path:
    """Save a report as <date>-<suite>.md, numbering it rather than overwriting one."""
    day = (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()
    directory.mkdir(parents=True, exist_ok=True)
    path, n = directory / f"{day}-{suite}.md", 1
    while path.exists():
        n += 1
        path = directory / f"{day}-{suite}-{n}.md"
    path.write_text(text, encoding="utf-8")
    return path


def confirm_spend(
    estimate_usd: float,
    *,
    budget_usd: float,
    yes: bool,
    ask: Callable[[str], str] = input,
    out: TextIO = sys.stdout,
) -> bool:
    """Whether to go ahead with a paid run: never over budget; otherwise on --yes or a "y"."""
    out.write(f"Estimated cost: ${estimate_usd:.2f} (budget ${budget_usd:.2f}).\n")
    if estimate_usd > budget_usd:
        out.write("The estimate is over the budget, so nothing will run.\n")
        return False
    if yes:
        return True
    return ask("Spend it? [y/N] ").strip().lower() == "y"
