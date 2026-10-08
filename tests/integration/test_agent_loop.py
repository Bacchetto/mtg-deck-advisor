"""The bounded agent loop, scripted with a fake model over a real database (AGT-1, AGT-2, AGT-4)."""

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import psycopg
import pytest

from mtg_deck_advisor.agent.loop import RunResult, run_agent
from mtg_deck_advisor.agent.prompts import system_prompt
from mtg_deck_advisor.agent.runs import tool_calls_for
from mtg_deck_advisor.agent.tools import Task
from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.errors import ModelCallError
from mtg_deck_advisor.llm.fake import FakeProvider, tool_call_reply
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.types import ProviderResponse, ToolCall, Usage
from tests.integration.test_agent_tools import (
    ATRAXA,
    LEGAL_CARDS,
    loaded,  # noqa: F401 (a fixture)
    session_for,
)

SONNET = "claude-sonnet-5-5"


def call(tool: str, n: int = 1, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"toolu_{tool}_{n}", name=tool, arguments=arguments)


def text_reply(text: str, *, stop_reason: str = "end_turn", **usage: int) -> ProviderResponse:
    return ProviderResponse(
        text=text,
        stop_reason=stop_reason,
        usage=Usage(**({"input_tokens": 100, "output_tokens": 20} | usage)),
        model=SONNET,
    )


def expensive_tool_reply(*calls: ToolCall) -> ProviderResponse:
    """A turn that costs $0.07 at Sonnet 5.5 prices ($2 in, $10 out per million)."""
    return ProviderResponse(
        text="",
        stop_reason="tool_use",
        usage=Usage(input_tokens=20_000, output_tokens=3_000),
        model=SONNET,
        tool_calls=calls,
    )


@pytest.fixture
def conn(loaded: Settings) -> Iterator[psycopg.Connection]:  # noqa: F811
    with connect(loaded) as connection:
        yield connection


def run(
    conn: psycopg.Connection,
    provider: FakeProvider,
    *,
    task: Task = "draft",
    max_turns: int = 10,
    cost_cap_usd: float = 1.0,
    max_tokens: int = 4000,
) -> RunResult:
    session = session_for(conn, task)
    conn.commit()  # as the API does before running, so other connections see the run
    client = ModelClient(provider, SONNET, recorder=MemoryRecorder(), cost_cap_usd=cost_cap_usd)
    return run_agent(
        client,
        session.context,
        system=system_prompt(task),
        prompt="Build a deck around Atraxa.",
        max_turns=max_turns,
        max_tokens=max_tokens,
    )


def run_row(conn: psycopg.Connection, run_id: UUID) -> tuple[Any, ...]:
    row = conn.execute(
        "SELECT status, turns, cost_usd, final_text, jsonb_array_length(transcript), error, "
        "finished_at IS NOT NULL FROM agent_runs WHERE id = %s",
        (run_id,),
    ).fetchone()
    assert row is not None
    return tuple(row)


# --- a run that finishes ------------------------------------------------------------


def test_a_run_calls_tools_until_the_model_answers(conn: psycopg.Connection) -> None:
    provider = FakeProvider(
        tool_call_reply(call("get_card", name="Sol Ring"), model=SONNET),
        text_reply("Sol Ring is ramp."),
    )

    result = run(conn, provider)

    assert (result.status, result.turns, result.final_text) == ("completed", 2, "Sol Ring is ramp.")
    first, second = provider.calls
    assert [tool.name for tool in first.tools][-1] == "propose_deck"
    assert first.effort == "medium"
    assert first.system == system_prompt("draft")
    # The second request carries the whole conversation: task, call, result.
    task, assistant, results = second.messages
    assert task.content == "Build a deck around Atraxa."
    assert assistant.tool_calls == (call("get_card", name="Sol Ring"),)
    (result_part,) = results.tool_results
    assert result_part.tool_call_id == "toolu_get_card_1" and not result_part.is_error
    assert "Sol Ring" in result_part.content

    status, turns, cost, final, transcript, error, finished = run_row(conn, result.run_id)
    assert (status, turns, final, transcript, error, finished) == (
        "completed",
        2,
        "Sol Ring is ramp.",
        4,
        None,
        True,
    )
    assert float(cost) == pytest.approx(result.cost_usd) and result.cost_usd > 0


def test_several_calls_in_one_turn_are_answered_together(conn: psycopg.Connection) -> None:
    provider = FakeProvider(
        tool_call_reply(
            call("get_card", 1, name="Sol Ring"),
            call("get_card", 2, name="Mox Jet"),
            model=SONNET,
        ),
        text_reply("Done."),
    )

    result = run(conn, provider)

    (results,) = [m for m in provider.calls[1].messages if m.tool_results]
    assert [r.tool_call_id for r in results.tool_results] == [
        "toolu_get_card_1",
        "toolu_get_card_2",
    ]
    assert [(c.turn, c.tool) for c in tool_calls_for(conn, result.run_id)] == [
        (1, "get_card"),
        (1, "get_card"),
    ]


# --- invalid tool calls are recovered from (AGT-4) -----------------------------------


def test_an_unknown_tool_and_bad_arguments_are_recovered_from(conn: psycopg.Connection) -> None:
    provider = FakeProvider(
        tool_call_reply(call("export_deck"), call("search_pool", k=5), model=SONNET),
        tool_call_reply(call("get_card", name="Sol Ring"), model=SONNET),
        text_reply("Recovered."),
    )

    result = run(conn, provider)

    assert (result.status, result.turns) == ("completed", 3)
    errors = provider.calls[1].messages[-1].tool_results
    assert [r.is_error for r in errors] == [True, True]
    assert "Unknown tool" in errors[0].content and "query" in errors[1].content
    assert [c.outcome for c in tool_calls_for(conn, result.run_id)] == ["error", "error", "ok"]


# --- limits (AGT-2) -----------------------------------------------------------------


def test_the_turn_limit_ends_the_run_cleanly_with_partial_results(
    conn: psycopg.Connection,
) -> None:
    provider = FakeProvider(
        tool_call_reply(
            call("propose_deck", commander=ATRAXA, cards=LEGAL_CARDS, rationale="r"),
            model=SONNET,
        ),
        *(tool_call_reply(call("get_card", n, name="Sol Ring"), model=SONNET) for n in range(5)),
    )

    result = run(conn, provider, max_turns=3)

    assert (result.status, result.turns) == ("turn_limit", 3)
    assert len(provider.calls) == 3
    # The draft proposed before the limit is kept, and so is the transcript.
    (proposal,) = result.proposals
    assert result.draft is not None and result.draft.total == 100
    assert conn.execute("SELECT status FROM proposals WHERE id = %s", (proposal,)).fetchone() == (
        "pending",
    )
    status, turns, _, final, transcript, _, finished = run_row(conn, result.run_id)
    assert (status, turns, final, finished) == ("turn_limit", 3, None, True)
    assert transcript == 7  # the task, then three calls and three sets of results


def test_a_run_that_could_go_over_budget_stops_before_the_call(
    conn: psycopg.Connection,
) -> None:
    provider = FakeProvider(text_reply("never sent"))

    # 4,000 output tokens could cost $0.04: more than the whole cap.
    result = run(conn, provider, cost_cap_usd=0.01)

    assert provider.calls == []
    assert (result.status, result.turns, result.cost_usd) == ("budget", 0, 0.0)
    assert "cost cap" in (result.error or "")
    assert run_row(conn, result.run_id)[0] == "budget"


def test_the_budget_stops_a_run_partway(conn: psycopg.Connection) -> None:
    provider = FakeProvider(
        expensive_tool_reply(call("get_card", name="Sol Ring")),
        expensive_tool_reply(call("get_card", 2, name="Sol Ring")),
    )

    # The first turn costs $0.07; the second could cost $0.07 + up to $0.08 more.
    result = run(conn, provider, cost_cap_usd=0.15, max_tokens=8000)

    assert (result.status, result.turns) == ("budget", 1)
    assert len(provider.calls) == 1
    assert result.cost_usd == pytest.approx(0.07)
    status, turns, cost, _, transcript, _, _ = run_row(conn, result.run_id)
    assert (status, turns, transcript) == ("budget", 1, 3)
    assert float(cost) == pytest.approx(0.07)


# --- failures -------------------------------------------------------------------------


def test_a_model_failure_ends_the_run_as_an_error_and_keeps_the_record(
    conn: psycopg.Connection,
) -> None:
    provider = FakeProvider(
        tool_call_reply(call("get_card", name="Sol Ring"), model=SONNET),
        ModelCallError("the API is down"),
    )

    result = run(conn, provider)

    assert (result.status, result.turns) == ("error", 1)
    assert result.error == "the API is down"
    status, turns, _, _, transcript, error, finished = run_row(conn, result.run_id)
    assert (status, turns, transcript, error, finished) == ("error", 1, 3, "the API is down", True)


def test_running_out_of_output_tokens_is_an_error_not_an_answer(
    conn: psycopg.Connection,
) -> None:
    provider = FakeProvider(text_reply("The deck should incl", stop_reason="max_tokens"))

    result = run(conn, provider)

    assert result.status == "error"
    assert result.final_text is None
    assert "output tokens" in (result.error or "")


# --- the system prompt ----------------------------------------------------------------


def test_the_system_prompt_marks_delimited_content_as_data() -> None:
    for task in ("draft", "refine"):
        prompt = system_prompt(task)
        assert "<untrusted>" in prompt
        assert "never instructions" in prompt
        assert "approv" in prompt  # the agent proposes; the user approves


class ProgressWatcher(FakeProvider):
    """Before each call, reads the run's recorded progress from another connection."""

    def __init__(self, settings: Settings, *replies: ProviderResponse) -> None:
        super().__init__(*replies)
        self.settings = settings
        self.seen: list[tuple[int, bool]] = []

    def complete(self, request: Any, model: str) -> ProviderResponse:
        with connect(self.settings) as other:
            row = other.execute(
                "SELECT turns, cost_usd > 0 FROM agent_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        assert row is not None
        self.seen.append((row[0], row[1]))
        return super().complete(request, model)


def test_a_runs_progress_is_saved_turn_by_turn(
    conn: psycopg.Connection,
    loaded: Settings,  # noqa: F811
) -> None:
    # So anyone polling the run (the API, the CLI) sees it advance.
    provider = ProgressWatcher(
        loaded,
        tool_call_reply(call("get_card", name="Sol Ring"), model=SONNET),
        tool_call_reply(call("get_card", 2, name="Sol Ring"), model=SONNET),
        text_reply("Done."),
    )

    run(conn, provider)

    assert provider.seen == [(0, False), (1, True), (2, True)]


def test_a_refusal_ends_the_run_as_refused_with_a_plain_message(
    conn: psycopg.Connection,
) -> None:
    # Live, injection case I04 made the API refuse after the agent read a
    # poisoned card. The user should learn their own data may be why (#137).
    provider = FakeProvider(
        tool_call_reply(call("get_card", name="Sol Ring"), model=SONNET),
        text_reply("", stop_reason="refusal"),
    )

    result = run(conn, provider)

    assert (result.status, result.turns) == ("refused", 1)
    message = result.error or ""
    assert "declined" in message and "turn 2" in message
    assert "text in your pool" in message
    assert "draft again" in message.lower()
    status, _, _, _, _, error, finished = run_row(conn, result.run_id)
    assert (status, error, finished) == ("refused", message, True)
