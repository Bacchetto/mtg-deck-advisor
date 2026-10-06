"""Summaries and variant-comparison reports for eval runs (#120, EVL-4, EVL-6)."""

import io
from datetime import UTC, datetime
from pathlib import Path

from mtg_deck_advisor.evaluation.runner import (
    CaseResult,
    EvalRun,
    ToolCounts,
    Variant,
    comparison_report,
    confirm_spend,
    save_report,
    summarize,
)

STARTED = datetime(2026, 10, 6, 15, 30, tzinfo=UTC)


def result(
    case: str,
    *,
    success: bool = True,
    cost: float = 0.01,
    status: str = "completed",
    latency: float = 2.0,
    turns: int = 3,
    errors: int = 0,
) -> CaseResult:
    return CaseResult(
        case_id=case,
        status=status,
        success=success,
        cost_usd=cost,
        latency_s=latency,
        turns=turns,
        tools=ToolCounts(ok=2, error=errors, rejected=0),
    )


def run(name: str, *results: CaseResult) -> EvalRun:
    return EvalRun(
        suite="rules",
        variant=Variant(name=name, model="claude-sonnet-5-5"),
        started_at=STARTED,
        finished_at=STARTED,
        commit="abc1234",
        budget_usd=1.0,
        results=list(results),
    )


def test_a_summary_reports_success_cost_latency_turns_and_tool_errors() -> None:
    summary = summarize(
        run(
            "sonnet",
            result("R1", latency=1.0, turns=2),
            result("R2", success=False, cost=0.03, latency=3.0, turns=4, errors=1),
            result("R3", status="skipped", success=False, cost=0.0, latency=0.0, turns=0),
        )
    )

    assert (summary.cases, summary.ran, summary.skipped) == (3, 2, 1)
    assert summary.success_rate == 0.5  # of the cases that ran
    assert summary.total_cost_usd == 0.04
    assert summary.mean_cost_usd == 0.02
    assert summary.median_latency_s == 2.0
    assert summary.mean_turns == 3.0
    assert summary.tool_errors == 1


def test_a_comparison_report_puts_variants_side_by_side_with_each_case() -> None:
    sonnet = run("sonnet", result("R1"), result("R2", success=False))
    haiku = run("haiku", result("R1", cost=0.002), result("R2"))

    report = comparison_report("Rules Q&A", [sonnet, haiku], now=STARTED)

    assert report.startswith("# Rules Q&A, 2026-10-06 15:30 UTC")
    assert "| sonnet | claude-sonnet-5-5 |" in report and "| haiku |" in report
    assert "| R2 | fail | pass |" in report  # per case, one column per variant
    assert "abc1234" in report  # the commit each run was made at


def test_reports_are_saved_with_a_date_and_never_overwrite(tmp_path: Path) -> None:
    first = save_report("one", "rules", directory=tmp_path, now=STARTED)
    second = save_report("two", "rules", directory=tmp_path, now=STARTED)

    assert first.name == "2026-10-06-rules.md"
    assert second.name == "2026-10-06-rules-2.md"
    assert first.read_text(encoding="utf-8") == "one"


def test_a_run_round_trips_through_json(tmp_path: Path) -> None:
    original = run("sonnet", result("R1"))

    path = original.save(tmp_path)

    assert path.parent == tmp_path / "rules"
    assert path.name == "2026-10-06T153000-sonnet.json"
    assert EvalRun.load(path) == original


def test_spending_needs_confirmation_unless_already_given() -> None:
    out = io.StringIO()

    assert confirm_spend(0.42, budget_usd=1.0, yes=True, ask=lambda _: "n", out=out)
    assert not confirm_spend(0.42, budget_usd=1.0, yes=False, ask=lambda _: "n", out=out)
    assert confirm_spend(0.42, budget_usd=1.0, yes=False, ask=lambda _: "y", out=out)
    assert "$0.42" in out.getvalue() and "$1.00" in out.getvalue()


def test_an_estimate_over_the_budget_is_refused_outright() -> None:
    out = io.StringIO()

    assert not confirm_spend(2.0, budget_usd=1.0, yes=True, ask=lambda _: "y", out=out)
    assert "over the budget" in out.getvalue()
