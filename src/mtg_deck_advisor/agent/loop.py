"""The bounded agent loop (AGT-1, AGT-2, AGT-4). See ADR 0013.

Native tool calling through ModelClient: send the conversation and the task's
tools; run every tool call the model makes and send all the results back in
one turn; repeat until the model answers without calling a tool.

Every run is bounded and every run ends with a record:

- **Turns.** At most `max_turns` model calls. A run still calling tools at the
  limit ends as `turn_limit`.
- **Money.** The client's cost cap is checked before each call against its
  worst case, so a run ends as `budget` before a call that could overspend,
  not after.
- **Failures.** A model or provider error ends the run as `error`; so does an
  answer cut off by the output-token limit, which is not an answer.

However a run ends, its transcript, turns and cost are saved in `agent_runs`,
and any proposals it made stay in `proposals`: a run cut short keeps what it
did (its partial results). Each turn is committed as it ends, so a crash loses
at most the turn in progress.

The transcript only ever grows: turns are appended, never edited, so the
model's thinking stays valid when sent back (ADR 0012).
"""

from dataclasses import dataclass
from uuid import UUID

import structlog

from mtg_deck_advisor.agent.runs import RunStatus, finish_run, record_progress
from mtg_deck_advisor.agent.tools import ToolContext, execute, tool_specs
from mtg_deck_advisor.deck.state import DeckState
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.errors import BudgetExceededError, ModelError
from mtg_deck_advisor.llm.types import Effort, Message, ModelRequest

log = structlog.get_logger(__name__)

# Room for thinking plus a whole-deck proposal (100 cards with names and counts).
DEFAULT_MAX_TOKENS = 16_000


@dataclass(frozen=True)
class RunResult:
    run_id: UUID
    status: RunStatus
    turns: int
    cost_usd: float
    # The model's closing answer; None unless the run completed.
    final_text: str | None
    proposals: list[UUID]
    # The deck as the run's latest proposal would leave it.
    draft: DeckState | None
    transcript: tuple[Message, ...]
    error: str | None = None


def run_agent(
    client: ModelClient,
    ctx: ToolContext,
    *,
    system: str,
    prompt: str,
    max_turns: int,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: Effort = "medium",
) -> RunResult:
    """Run the agent on `prompt` until it answers or hits a limit, and record the run.

    The run must already be started (`ctx.run_id`). The client's cost cap is the
    run's budget; only what this run spends is counted.
    """
    spent_before = client.spent_usd
    tools = tool_specs(ctx.task)
    messages: list[Message] = [Message(role="user", content=prompt)]
    status: RunStatus = "turn_limit"
    final_text: str | None = None
    error: str | None = None
    turns = 0

    try:
        while turns < max_turns:
            try:
                response = client.generate(
                    ModelRequest(
                        purpose=f"agent_{ctx.task}",
                        system=system,
                        messages=tuple(messages),
                        tools=tools,
                        max_tokens=max_tokens,
                        effort=effort,
                    )
                )
            except BudgetExceededError as exc:
                status, error = "budget", str(exc)
                break
            except ModelError as exc:
                status, error = "error", str(exc)
                break
            turns += 1
            messages.append(response.as_message())

            if not response.tool_calls:
                if response.stop_reason == "max_tokens":
                    status = "error"
                    error = f"the model ran out of output tokens ({max_tokens}) mid-answer"
                else:
                    status, final_text = "completed", response.text
                break

            results = tuple(execute(ctx, call, turn=turns) for call in response.tool_calls)
            messages.append(Message(role="user", tool_results=results))
            cost = client.spent_usd - spent_before
            record_progress(ctx.conn, ctx.run_id, turns=turns, cost_usd=cost)
            ctx.conn.commit()
    except Exception as exc:
        # A bug, not a model failure: record the run as it stands, then raise.
        ctx.conn.rollback()
        _finish(ctx, client, spent_before, "error", turns, None, messages, repr(exc))
        raise

    result = _finish(ctx, client, spent_before, status, turns, final_text, messages, error)
    log.info(
        "agent_run",
        run_id=str(ctx.run_id),
        task=ctx.task,
        status=status,
        turns=turns,
        cost_usd=round(result.cost_usd, 6),
        proposals=len(ctx.proposals),
    )
    return result


def _finish(
    ctx: ToolContext,
    client: ModelClient,
    spent_before: float,
    status: RunStatus,
    turns: int,
    final_text: str | None,
    messages: list[Message],
    error: str | None,
) -> RunResult:
    cost = client.spent_usd - spent_before
    finish_run(
        ctx.conn,
        ctx.run_id,
        status=status,
        turns=turns,
        cost_usd=cost,
        final_text=final_text,
        transcript=[message.model_dump(mode="json") for message in messages],
        error=error,
    )
    ctx.conn.commit()
    return RunResult(
        run_id=ctx.run_id,
        status=status,
        turns=turns,
        cost_usd=cost,
        final_text=final_text,
        proposals=list(ctx.proposals),
        draft=ctx.draft,
        transcript=tuple(messages),
        error=error,
    )
