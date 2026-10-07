"""Tools the owner runs by hand load only what they need (#122).

`hand_grade` reads saved run files. It once imported the deck task harness
and so the whole agent, the Anthropic SDK included: hundreds of modules,
which took minutes to load from a network drive before the first deck showed.
"""

import subprocess
import sys

HEAVY = (
    "anthropic",
    "mtg_deck_advisor.agent.flows",
    "mtg_deck_advisor.retrieval.search",
    "psycopg",
)


def loaded_after_import(module: str) -> set[str]:
    code = f"import sys, {module}; print('\\n'.join(sys.modules))"
    # Our own interpreter and a fixed module name: nothing untrusted.
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    return set(out.stdout.split())


def test_hand_grading_doesnt_load_the_agent_or_the_model_sdk() -> None:
    loaded = loaded_after_import("mtg_deck_advisor.evaluation.hand_grade")

    assert loaded.isdisjoint(HEAVY), sorted(loaded & set(HEAVY))


def test_run_results_can_be_read_without_the_agent() -> None:
    loaded = loaded_after_import("mtg_deck_advisor.evaluation.results")

    assert loaded.isdisjoint(HEAVY), sorted(loaded & set(HEAVY))


def test_the_results_search_modes_match_the_search_codes() -> None:
    from mtg_deck_advisor.evaluation.results import SearchMode as Recorded
    from mtg_deck_advisor.retrieval.search import SearchMode as Searched

    assert Recorded.__value__ == Searched.__value__
