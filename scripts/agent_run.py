"""Run the agent for real, one step at a time, until the CLI and API exist (Milestone 6).

    python scripts/agent_run.py draft evals/datasets/pool_300.txt --request "..."
    python scripts/agent_run.py show PROPOSAL_ID
    python scripts/agent_run.py approve PROPOSAL_ID [--note "..."]
    python scripts/agent_run.py reject PROPOSAL_ID --reason "..."
    python scripts/agent_run.py apply PROPOSAL_ID
    python scripts/agent_run.py refine DECK_ID --request "..."
    python scripts/agent_run.py approve-export DECK_ID
    python scripts/agent_run.py export DECK_ID
    python scripts/agent_run.py rules "question" ["question" ...] [--cap 0.25]

The agent steps (draft, refine, rules) call the configured model and cost
money: AGENT_MODEL, capped per run at AGENT_COST_CAP_USD (or --cap). Set
RECORD_RESPONSES=true to save every response for the no-key demo. Approving,
applying and exporting are the user's steps; they call no model.

Each agent step prints its status, turns, cost, tokens and wall-clock time.
"""

import argparse
import json
import sys
import time
from uuid import UUID

import psycopg

from mtg_deck_advisor.agent.flows import (
    AgentServices,
    answer_rules_question,
    build_services,
    draft_deck,
    import_pool,
    refine_deck,
)
from mtg_deck_advisor.agent.loop import RunResult
from mtg_deck_advisor.config import Settings, get_settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.deck.state import DeckState
from mtg_deck_advisor.deck.store import decklist
from mtg_deck_advisor.guardrails.approvals import (
    ApprovalError,
    apply_proposal,
    approve_export,
    approve_proposal,
    export_deck,
    reject_proposal,
)
from mtg_deck_advisor.observability.logging import configure_logging
from mtg_deck_advisor.observability.tracing import traced


def report(conn: psycopg.Connection, run: RunResult, seconds: float) -> None:
    tokens = conn.execute(
        """
        SELECT count(*), sum(input_tokens), sum(output_tokens),
               sum(cache_read_input_tokens), sum(cache_creation_input_tokens)
        FROM model_calls m JOIN agent_runs r ON r.trace_id = m.trace_id
        WHERE r.id = %s AND m.purpose LIKE 'agent_%%'
        """,
        (run.run_id,),
    ).fetchone()
    tools = conn.execute(
        "SELECT outcome, count(*) FROM tool_calls WHERE run_id = %s GROUP BY outcome",
        (run.run_id,),
    ).fetchall()
    print(
        f"run {run.run_id}: {run.status} after {run.turns} turns, "
        f"${run.cost_usd:.4f}, {seconds:.0f} s"
    )
    if tokens:
        calls, inp, out, read, write = tokens
        print(
            f"  model calls {calls}: input {inp}, output {out}, "
            f"cache read {read}, cache write {write}"
        )
    print(f"  tool calls: {dict(tools)}")
    print(f"  proposals: {[str(p) for p in run.proposals]}")
    if run.error:
        print(f"  error: {run.error}")
    if run.final_text:
        print(f"\n{run.final_text}\n")


def services(conn: psycopg.Connection, settings: Settings, cap: float | None) -> AgentServices:
    if cap is not None:
        settings = settings.model_copy(update={"agent_cost_cap_usd": cap})
    return build_services(conn, settings)


def show(conn: psycopg.Connection, proposal: UUID) -> None:
    row = conn.execute(
        "SELECT kind, status, rationale, citations, validation, payload "
        "FROM proposals WHERE id = %s",
        (proposal,),
    ).fetchone()
    if row is None:
        sys.exit(f"no proposal {proposal}")
    kind, status, rationale, citations, validation, payload = row
    print(f"{kind} proposal {proposal}: {status}\n\nRationale: {rationale}\nCitations: {citations}")
    if validation and validation.get("problems"):
        print("Problems:\n" + "\n".join(f"- {p['message']}" for p in validation["problems"]))
    if kind == "changes":
        print(f"Request: {json.dumps(payload['request'], indent=1)}")
    if payload.get("deck"):
        print("\n" + decklist(conn, DeckState.from_dict(payload["deck"])))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--cap", type=float, help="this run's cost cap in USD")
    commands = parser.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("draft")
    draft.add_argument("pool_file")
    draft.add_argument("--request", default="")
    for name in ("show", "approve", "reject", "apply"):
        command = commands.add_parser(name)
        command.add_argument("proposal", type=UUID)
        if name == "approve":
            command.add_argument("--note")
        if name == "reject":
            command.add_argument("--reason", required=True)
    refine = commands.add_parser("refine")
    refine.add_argument("deck", type=UUID)
    refine.add_argument("--request", required=True)
    for name in ("approve-export", "export"):
        commands.add_parser(name).add_argument("deck", type=UUID)
    rules = commands.add_parser("rules")
    rules.add_argument("questions", nargs="+")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    with traced(), connect(settings) as conn:
        try:
            if args.command == "draft":
                with open(args.pool_file, encoding="utf-8") as file:
                    imported = import_pool(conn, file.read(), name=args.pool_file)
                print(f"pool {imported.pool_id}; unresolved: {imported.unresolved}")
                started = time.perf_counter()
                result = draft_deck(
                    services(conn, settings, args.cap), imported.pool_id, request=args.request
                )
                print(f"deck {result.deck_id}")
                report(conn, result.run, time.perf_counter() - started)
            elif args.command == "refine":
                started = time.perf_counter()
                run = refine_deck(
                    services(conn, settings, args.cap), args.deck, request=args.request
                )
                report(conn, run, time.perf_counter() - started)
            elif args.command == "rules":
                for question in args.questions:
                    started = time.perf_counter()
                    with traced():  # one trace per question, so each report counts its own
                        answer = answer_rules_question(services(conn, settings, args.cap), question)
                    print(f"\nQ: {question}")
                    report(conn, answer.run, time.perf_counter() - started)
                    if answer.found:
                        print(f"A: {answer.answer}")
                        for rule in answer.citations:
                            print(f"  [{rule.number}] {rule.text[:200]}")
                    else:
                        print(f"Not found: {answer.reason}")
            elif args.command == "show":
                show(conn, args.proposal)
            elif args.command == "approve":
                print(approve_proposal(conn, args.proposal, note=args.note))
            elif args.command == "reject":
                print(reject_proposal(conn, args.proposal, reason=args.reason))
            elif args.command == "apply":
                print(f"applied as version {apply_proposal(conn, args.proposal)}")
            elif args.command == "approve-export":
                print(approve_export(conn, args.deck))
            elif args.command == "export":
                print(export_deck(conn, args.deck))
        except ApprovalError as exc:
            conn.commit()  # keep the refusal's audit entry
            sys.exit(f"refused: {exc}")


if __name__ == "__main__":
    main()
