"""Running eval cases against variants, with metrics from the run records (#120, EVL-4).

Each case runs the real agent flow on a scripted model, so no call is paid.
"""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

from typing import Any

import psycopg
import pytest

from mtg_deck_advisor.agent import tools
from mtg_deck_advisor.agent.flows import AgentServices, answer_rules_question
from mtg_deck_advisor.agent.runs import Task
from mtg_deck_advisor.evaluation.runner import (
    PROMPT_VARIANTS,
    CaseResult,
    Variant,
    result_from_run,
    run_suite,
    variant_services,
)
from mtg_deck_advisor.llm.fake import FakeEmbedder, FakeProvider
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.retrieval.search import search_rules
from tests.integration.test_agent_flows import answer, call, calls, conn  # noqa: F401
from tests.integration.test_agent_tools import loaded  # noqa: F401

QUESTIONS = {"R1": "What is color identity?", "R2": "Does reminder text count?"}


def scripted_answer(cited: str = "903.4") -> list[Any]:
    return [
        calls(call("search_rules", question="color identity", k=3)),
        answer(f"Within the commander's colors.\nCitations: {cited}"),
    ]


def services_for(
    conn: psycopg.Connection, variant: Variant, provider: FakeProvider
) -> AgentServices:
    return variant_services(
        conn,
        variant,
        provider=provider,
        embedder=FakeEmbedder(),
        reranker=None,
        recorder=MemoryRecorder(),
        cost_cap_usd=1.0,
        max_turns=10,
    )


def rules_case(conn: psycopg.Connection, services: AgentServices, case_id: str) -> CaseResult:
    answered = answer_rules_question(services, QUESTIONS[case_id])
    return result_from_run(
        conn,
        answered.run.run_id,
        case_id,
        success=answered.found,
        details={"cited": [c.number for c in answered.citations]},
    )


def test_a_suite_records_each_cases_metrics_from_its_run(conn: psycopg.Connection) -> None:
    provider = FakeProvider(*scripted_answer(), *scripted_answer(cited="999.9"))
    variant = Variant(name="sonnet", model="claude-sonnet-5-5")
    services = services_for(conn, variant, provider)

    run = run_suite(
        "rules", ["R1", "R2"], variant, lambda c: rules_case(conn, services, c), budget_usd=1.0
    )

    first, second = run.results
    assert (first.case_id, first.status, first.success) == ("R1", "completed", True)
    assert first.turns == 2 and first.tools.ok == 1 and first.cost_usd > 0
    assert first.latency_s > 0
    assert first.details == {"cited": ["903.4"]}
    assert second.success is False  # it cited a rule it never retrieved
    assert run.total_cost_usd == pytest.approx(first.cost_usd + second.cost_usd)
    assert run.finished_at is not None


def test_a_variant_sets_the_model_and_the_system_prompt(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(PROMPT_VARIANTS, "terse", lambda task: f"Be terse. ({task})")
    provider = FakeProvider(*scripted_answer())
    variant = Variant(name="haiku-terse", model="claude-haiku-4-5", prompt="terse")

    run_suite(
        "rules",
        ["R1"],
        variant,
        lambda c: rules_case(conn, services_for(conn, variant, provider), c),
        budget_usd=1.0,
    )

    request = provider.calls[0]
    assert request.system == "Be terse. (rules)"
    row = conn.execute("SELECT model FROM agent_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    assert row is not None and row[0] == "claude-haiku-4-5"


def test_a_variant_sets_how_the_agents_tools_search(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    modes: list[str] = []
    real = search_rules

    def spy(*args: Any, **kwargs: Any) -> Any:
        modes.append(kwargs.get("mode", "vector"))
        return real(*args, **kwargs)

    monkeypatch.setattr(tools, "search_rules", spy)
    provider = FakeProvider(*scripted_answer())
    variant = Variant(name="hybrid-rules", model="claude-sonnet-5-5", rules_search="hybrid")

    rules_case(conn, services_for(conn, variant, provider), "R1")

    assert modes == ["hybrid"]


def test_cases_beyond_the_budget_are_skipped_not_run(conn: psycopg.Connection) -> None:
    provider = FakeProvider(*scripted_answer())  # enough for one case only
    variant = Variant(name="sonnet", model="claude-sonnet-5-5")
    services = services_for(conn, variant, provider)

    run = run_suite(
        "rules", ["R1", "R2"], variant, lambda c: rules_case(conn, services, c), budget_usd=1e-9
    )

    assert [r.status for r in run.results] == ["completed", "skipped"]
    assert len(provider.calls) == 2  # R1's two turns; R2 never started


def test_a_case_that_raises_is_recorded_as_an_error(conn: psycopg.Connection) -> None:
    variant = Variant(name="sonnet", model="claude-sonnet-5-5")

    def broken(case_id: str) -> CaseResult:
        raise RuntimeError("the grader fell over")

    run = run_suite("rules", ["R1"], variant, broken, budget_usd=1.0)

    (only,) = run.results
    assert (only.status, only.success) == ("error", False)
    assert only.error is not None and "the grader fell over" in only.error


def test_task_type_is_known_to_the_prompt_variants() -> None:
    task: Task = "rules"
    assert PROMPT_VARIANTS["default"](task)
