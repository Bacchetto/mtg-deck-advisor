from pathlib import Path

import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.llm.anthropic import AnthropicProvider
from mtg_deck_advisor.llm.factory import build_provider


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, database_url="postgresql://x:y@127.0.0.1/db", **values)  # type: ignore[arg-type]


def test_the_anthropic_provider_is_built_from_settings() -> None:
    provider = build_provider(settings(model_provider="anthropic", anthropic_api_key="sk-ant-x"))

    assert isinstance(provider, AnthropicProvider)


def test_the_anthropic_provider_needs_a_key() -> None:
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        build_provider(settings(model_provider="anthropic"))


def test_the_ollama_provider_is_built_from_settings() -> None:
    from mtg_deck_advisor.llm.ollama import OllamaProvider

    provider = build_provider(settings(model_provider="ollama", model_name="qwen3:8b"))

    assert isinstance(provider, OllamaProvider)


def test_the_embedder_uses_the_configured_ollama_model() -> None:
    from mtg_deck_advisor.llm.factory import build_embedder

    embedder = build_embedder(settings(embedding_model="nomic-embed-text"))

    assert embedder.model == "nomic-embed-text"


def test_the_replay_provider_is_built_from_settings(tmp_path: Path) -> None:
    from mtg_deck_advisor.llm.replay import ReplayProvider

    provider = build_provider(settings(model_provider="replay", replay_dir=tmp_path))

    assert isinstance(provider, ReplayProvider)


def test_recording_wraps_the_real_provider(tmp_path: Path) -> None:
    from mtg_deck_advisor.llm.replay import RecordingProvider

    provider = build_provider(
        settings(
            model_provider="ollama",
            model_name="qwen3:14b",
            record_responses=True,
            replay_dir=tmp_path,
        )
    )

    assert isinstance(provider, RecordingProvider)
    assert provider.name == "ollama"


def test_recording_while_replaying_makes_no_sense() -> None:
    with pytest.raises(ValueError, match="RECORD_RESPONSES"):
        build_provider(settings(model_provider="replay", record_responses=True))


# --- the agent on a local model (#152) -----------------------------------------------------


def test_the_ollama_provider_gets_the_agents_context_length() -> None:
    from mtg_deck_advisor.llm.ollama import OllamaProvider

    provider = build_provider(
        settings(model_provider="ollama", model_name="qwen3:14b", ollama_agent_num_ctx=8192)
    )

    assert isinstance(provider, OllamaProvider)
    assert provider._agent_num_ctx == 8192


def test_an_eval_variant_names_its_provider() -> None:
    from mtg_deck_advisor.evaluation.results import Variant
    from mtg_deck_advisor.evaluation.runner import variant_provider
    from mtg_deck_advisor.llm.ollama import OllamaProvider

    mine = settings(model_provider="anthropic", anthropic_api_key="sk-ant-x")
    claude = Variant(name="sonnet", model="claude-sonnet-5-5")
    local = Variant(name="qwen3-14b", model="qwen3:14b", provider="ollama")

    assert claude.provider == "anthropic"  # what every saved run was
    assert isinstance(variant_provider(mine, claude), AnthropicProvider)
    assert isinstance(variant_provider(mine, local), OllamaProvider)


def test_the_suites_offer_the_local_model() -> None:
    from mtg_deck_advisor.evaluation import deck_tasks, rules_qa

    for suite in (rules_qa, deck_tasks):
        variant = suite.VARIANTS["qwen3-14b"]
        assert (variant.model, variant.provider) == ("qwen3:14b", "ollama")
    # A local model costs nothing to run; only the Claude grading is estimated.
    tasks = [t for t in deck_tasks.load_tasks() if t.id == "T01"]
    assert deck_tasks.estimate_usd([deck_tasks.VARIANTS["qwen3-14b"]], tasks) == (
        deck_tasks.GRADING_COST
    )


def test_the_local_model_can_be_run_with_a_tool_first_nudge() -> None:
    # With the shipped rules prompt, qwen3:14b answers from memory without
    # searching; with one more line, it calls search_rules (#152).
    from mtg_deck_advisor.agent.prompts import system_prompt
    from mtg_deck_advisor.evaluation import deck_tasks, rules_qa
    from mtg_deck_advisor.evaluation.runner import PROMPT_VARIANTS

    for task in ("rules", "draft", "refine"):
        nudged = PROMPT_VARIANTS["tool-first"](task)  # type: ignore[arg-type]
        assert nudged.startswith(system_prompt(task))  # type: ignore[arg-type]
        assert "always call a tool" in nudged.removeprefix(system_prompt(task))  # type: ignore[arg-type]
    for suite in (rules_qa, deck_tasks):
        shipped, nudged_variant = suite.VARIANTS["qwen3-14b"], suite.VARIANTS["qwen3-14b-nudged"]
        assert shipped.prompt == "default" and nudged_variant.prompt == "tool-first"
        assert (nudged_variant.model, nudged_variant.provider) == ("qwen3:14b", "ollama")
