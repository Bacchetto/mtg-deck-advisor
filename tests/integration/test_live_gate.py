"""The live CI eval: a few rules questions on the real model, gated by code (#124, EVL-5).

Here the model is scripted, so nothing is paid: these tests check the
gate's mechanics (the pinned embedder, the metrics, the degraded prompt,
the thresholds) and that a model that stops citing fails it.
"""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

import psycopg
import pytest

from mtg_deck_advisor.agent.flows import AgentServices
from mtg_deck_advisor.evaluation.gate import check, load_thresholds
from mtg_deck_advisor.evaluation.live_gate import (
    DEGRADED_RULES,
    LIVE_CASES,
    LIVE_THRESHOLDS,
    PinnedEmbedder,
    live_metrics,
    run_live_gate,
)
from mtg_deck_advisor.evaluation.rules_qa import RulesCase, load_cases
from mtg_deck_advisor.evaluation.runner import PROMPT_VARIANTS, EvalRun, Variant, variant_services
from mtg_deck_advisor.evaluation.snapshot import SnapshotEmbedder
from mtg_deck_advisor.llm.fake import FakeEmbedder, FakeProvider
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.types import ProviderResponse
from mtg_deck_advisor.retrieval.text import query_text
from tests.integration.test_agent_flows import answer, call, calls, conn  # noqa: F401
from tests.integration.test_agent_tools import loaded  # noqa: F401

ANSWERABLE = RulesCase(
    id="R04",
    kind="commander",
    question="Does reminder text count toward color identity?",
    answerable=True,
    key_facts=["No."],
    relevant=["903.4c"],
    also_acceptable=[],
    notes="",
)
UNANSWERABLE = RulesCase(
    id="N01",
    kind="not_found",
    question="Is it OK to use proxies?",
    answerable=False,
    key_facts=[],
    relevant=[],
    also_acceptable=[],
    notes="Not in the rules.",
)


def gate(
    conn: psycopg.Connection, *replies: ProviderResponse, prompt: str = "default"
) -> tuple[EvalRun, FakeProvider]:
    provider = FakeProvider(*replies)
    variant = Variant(name="sonnet", model="claude-sonnet-5-5", prompt=prompt)

    def services_for() -> AgentServices:
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

    run = run_live_gate(conn, services_for, [ANSWERABLE, UNANSWERABLE], variant, budget_usd=0.10)
    return run, provider


# --- the set and its thresholds ------------------------------------------------------------


def test_the_live_set_is_eight_baseline_passes_with_two_unanswerable() -> None:
    cases = {c.id: c for c in load_cases()}

    assert len(LIVE_CASES) == 8
    assert all(case_id in cases for case_id in LIVE_CASES)
    assert sum(not cases[c].answerable for c in LIVE_CASES) == 2


def test_the_live_thresholds_are_their_own_file() -> None:
    # The free gate runs on every PR and would report these as unmeasured.
    thresholds = load_thresholds(LIVE_THRESHOLDS)

    assert thresholds and all(name.startswith("live.") for name in thresholds)
    assert not any(name.startswith("live.") for name in load_thresholds())


# --- searching without the embedding model ----------------------------------------------------


def test_a_pinned_embedder_answers_any_search_with_the_cases_question() -> None:
    question = query_text("qwen3-embedding:8b", "rules", "How many cards?")
    snapshot = SnapshotEmbedder("qwen3-embedding:8b", {question: [1.0, 0.0]})
    embedder = PinnedEmbedder(snapshot)

    embedder.pin("How many cards?")
    vectors = embedder.embed([query_text("qwen3-embedding:8b", "rules", "deck size commander")])

    assert vectors == [[1.0, 0.0]]
    assert embedder.model == "qwen3-embedding:8b"
    embedder.pin(None)
    with pytest.raises(KeyError):
        embedder.embed(["anything new"])


# --- the gate ------------------------------------------------------------------------------


def test_a_model_that_cites_and_abstains_passes(conn: psycopg.Connection) -> None:
    run, _ = gate(
        conn,
        calls(call("search_rules", question="reminder text color identity", k=5)),
        answer("No, reminder text is ignored.\nCitations: 903.4c"),
        calls(call("search_rules", question="proxies", k=5)),
        answer("NOT FOUND. The rules don't cover proxies."),
    )

    metrics = live_metrics(run)

    assert metrics == {
        "live.rules.citation_hit": 1.0,
        "live.rules.citation_precision": 1.0,
        "live.rules.abstention": 1.0,
        "live.rules.answered": 1.0,
    }
    assert all(r.grading_cost_usd == 0 for r in run.results)  # no grader: code only
    assert check(metrics, {"live.rules.citation_hit": 0.8}) == []


def test_the_degraded_prompt_drops_citations_and_fails_the_gate(conn: psycopg.Connection) -> None:
    run, provider = gate(
        conn,
        calls(call("search_rules", question="reminder text color identity", k=5)),
        answer("No, reminder text is ignored."),  # no citations: the answer is withheld
        calls(call("search_rules", question="proxies", k=5)),
        answer("Proxies are a matter for your playgroup."),
        prompt="degraded",
    )

    metrics = live_metrics(run)

    assert provider.calls[0].system == DEGRADED_RULES
    assert "Citations" not in DEGRADED_RULES and "NOT FOUND" not in DEGRADED_RULES
    assert metrics["live.rules.answered"] == 0.0 and metrics["live.rules.abstention"] == 0.0
    failures = check(metrics, load_thresholds(LIVE_THRESHOLDS))
    assert any(f.startswith("live.rules.answered") for f in failures)


def test_the_degraded_prompt_is_registered_for_variants() -> None:
    assert PROMPT_VARIANTS["degraded"]("rules") == DEGRADED_RULES


def test_the_snapshot_has_every_live_question() -> None:
    from mtg_deck_advisor.evaluation.snapshot import SNAPSHOT_DIR, _read_jsonl

    queries = {r["query"] for r in _read_jsonl(SNAPSHOT_DIR / "queries.jsonl.gz")}
    cases = {c.id: c for c in load_cases()}

    assert {cases[c].question for c in LIVE_CASES} <= queries
