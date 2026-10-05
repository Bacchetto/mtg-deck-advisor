"""`mtg-advisor`: a thin command-line client of the HTTP API.

    mtg-advisor pool add pool.txt [--csv] [--name NAME]
    mtg-advisor pool list | pool show POOL
    mtg-advisor draft POOL [--request TEXT] [--name NAME] [--no-wait]
    mtg-advisor refine DECK --request TEXT [--no-wait]
    mtg-advisor run RUN
    mtg-advisor proposal PROPOSAL
    mtg-advisor approve PROPOSAL [--note TEXT] | reject PROPOSAL --reason TEXT
    mtg-advisor apply PROPOSAL
    mtg-advisor deck DECK
    mtg-advisor approve-export DECK [--version N] | export DECK [--version N]
    mtg-advisor ask "QUESTION"

Every command is one or more API calls (`API_URL`, the local server by
default), so the CLI and the API can't drift apart: the API does the work and
the checking, the CLI only formats. Drafts and refines run on the server; the
CLI polls the run and shows its progress until it ends, unless --no-wait.
`--json` prints the API's answer instead of text. A refused step or an
unreachable API exits with code 1 and the reason; a usage error with 2.
"""

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

import httpx2

from mtg_deck_advisor.config import get_settings

POLL_SECONDS = 2.0


class ApiError(Exception):
    """The API refused or failed a request; the message is its status and reason."""


class Api:
    def __init__(self, http: httpx2.Client) -> None:
        self._http = http

    def get(self, path: str) -> Any:
        return self._send("GET", path).json()

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self._send("POST", path, body or {}).json()

    def text(self, path: str) -> str:
        return self._send("GET", path).text

    def _send(self, method: str, path: str, body: dict[str, Any] | None = None) -> httpx2.Response:
        response = self._http.request(method, path, json=body)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            if isinstance(detail, list):  # a validation error: one entry per field
                detail = "; ".join(f"{'.'.join(map(str, d['loc']))}: {d['msg']}" for d in detail)
            raise ApiError(f"{response.status_code} {detail}")
        return response


class Printer:
    def __init__(self, out: TextIO, as_json: bool) -> None:
        self.out = out
        self.as_json = as_json

    def line(self, text: str = "") -> None:
        if not self.as_json:
            self.out.write(text + "\n")

    def result(self, data: Any, show: Callable[[Any], None]) -> None:
        if self.as_json:
            self.out.write(json.dumps(data, indent=2) + "\n")
        else:
            show(data)


def main(
    argv: Sequence[str] | None = None,
    *,
    http: httpx2.Client | None = None,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse has printed the usage problem
        return exc.code if isinstance(exc.code, int) else 2
    client = http or httpx2.Client(base_url=get_settings().api_url, timeout=60)
    printer = Printer(out, args.json)
    try:
        args.handler(Api(client), args, printer, sleep)
    except ApiError as exc:
        err.write(f"error: {exc}\n")
        return 1
    except httpx2.TransportError as exc:
        err.write(
            f"error: cannot reach the API at {client.base_url} ({exc}). Is it running? Start it "
            "with `docker compose up -d` or `python -m mtg_deck_advisor.api`.\n"
        )
        return 1
    return 0


def run() -> None:
    """The `mtg-advisor` console script."""
    sys.exit(main())


# --- commands ------------------------------------------------------------------------------

type Sleep = Callable[[float], None]


def pool_add(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    path = Path(args.file)
    body = {
        "name": args.name or path.name,
        "format": "csv" if args.csv else "text",
        "content": path.read_text(encoding="utf-8"),
    }
    created = api.post("/pools", body)

    def show(c: Any) -> None:
        p.line(f"pool {c['pool_id']}: {c['cards']} cards ({c['distinct']} different)")
        if c["unresolved"]:
            p.line(f"unresolved, left out: {', '.join(c['unresolved'])}")
        for problem in c["problems"]:
            p.line(f"unreadable: {problem}")
        p.line(f"next: mtg-advisor draft {c['pool_id']}")

    p.result(created, show)


def pool_list(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    def show(pools: Any) -> None:
        for pool in pools:
            counts = f"{pool['cards']} cards ({pool['distinct']} different)"
            p.line(f"{pool['id']}  {pool['name']}: {counts}")
        if not pools:
            p.line("no pools yet: add one with `mtg-advisor pool add FILE`")

    p.result(api.get("/pools"), show)


def pool_show(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    def show(pool: Any) -> None:
        p.line(f"{pool['name']}: {pool['cards']} cards ({pool['distinct']} different)")
        p.line("possible commanders:")
        for commander in pool["commanders"]:
            p.line(f"  {commander['name']} ({commander['color_identity']})")

    p.result(api.get(f"/pools/{args.pool}"), show)


def draft(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    body = {"request": args.request} | ({"name": args.name} if args.name else {})
    started = api.post(f"/pools/{args.pool}/drafts", body)
    _follow(api, started, args, p, sleep)


def refine(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    started = api.post(f"/decks/{args.deck}/refinements", {"request": args.request})
    _follow(api, started, args, p, sleep)


def show_run(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    p.result(api.get(f"/runs/{args.run}"), lambda run: _show_run(run, p))


def proposal(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    def show(pr: Any) -> None:
        p.line(f"{pr['kind']} proposal {pr['id']}: {pr['status']}")
        p.line(f"Rationale: {pr['rationale']}")
        if pr["citations"]:
            # "; " because card names can contain commas.
            p.line(f"Citations: {'; '.join(pr['citations'])}")
        if pr["problems"]:
            p.line("Problems:")
            for problem in pr["problems"]:
                rule = f" (rule {problem['rule']})" if problem.get("rule") else ""
                p.line(f"  - {problem['message']}{rule}")
        if pr["changes"]:
            for card in pr["changes"]["remove"]:
                p.line(f"  - {card['count']} {card['name']}")
            for card in pr["changes"]["add"]:
                p.line(f"  + {card['count']} {card['name']}")
        if pr["decklist"]:
            p.line()
            p.line(pr["decklist"])
        if pr["status"] == "pending":
            p.line()
            p.line(f"next: mtg-advisor approve {pr['id']}  (or reject {pr['id']} --reason ...)")

    p.result(api.get(f"/proposals/{args.proposal}"), show)


def approve(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    approval = api.post(f"/proposals/{args.proposal}/approval", {"note": args.note})
    p.result(approval, lambda a: p.line(f"approved; next: mtg-advisor apply {args.proposal}"))


def reject(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    rejection = api.post(f"/proposals/{args.proposal}/rejection", {"reason": args.reason})
    p.result(rejection, lambda a: p.line("rejected"))


def apply(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    applied = api.post(f"/proposals/{args.proposal}/application")
    p.result(applied, lambda a: p.line(f"applied as version {a['version']} of deck {a['deck_id']}"))


def deck(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    def show(d: Any) -> None:
        if d["version"] is None:
            p.line(f"{d['name']}: no saved version yet")
            return
        p.line(f"{d['name']}, version {d['version']}:")
        for v in d["versions"]:
            source = f"from proposal {v['proposal_id']}" if v["proposal_id"] else "saved directly"
            p.line(f"  version {v['version']}, {v['created_at'][:16]}, {source}")
        p.line()
        p.line(d["decklist"])

    p.result(api.get(f"/decks/{args.deck}"), show)


def approve_export(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    version = args.version or _latest_version(api, args.deck)
    approval = api.post(f"/decks/{args.deck}/versions/{version}/export-approval")
    p.result(
        approval,
        lambda a: p.line(
            f"export of version {version} approved; next: mtg-advisor export {args.deck}"
        ),
    )


def export(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    version = args.version or _latest_version(api, args.deck)
    # A plain decklist either way: it's what deck sites import.
    p.out.write(api.text(f"/decks/{args.deck}/versions/{version}/export") + "\n")


def ask(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    def show(a: Any) -> None:
        if not a["found"]:
            p.line(f"Not found: {a['reason']}")
            return
        p.line(a["answer"])
        p.line()
        for rule in a["citations"]:
            p.line(f"[{rule['number']}] {rule['text']}")

    p.result(api.post("/rules/answers", {"question": args.question}), show)


# --- helpers ---------------------------------------------------------------------------------


def _follow(api: Api, started: Any, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    """Wait for a started run, showing its progress, then show how it ended."""
    if args.no_wait:
        p.result(
            started,
            lambda s: p.line(
                f"run {s['run_id']} started; follow it with `mtg-advisor run {s['run_id']}`"
            ),
        )
        return
    p.line(f"run {started['run_id']} started")
    seen: tuple[int, float] | None = None
    while True:
        sleep(POLL_SECONDS)
        run = api.get(f"/runs/{started['run_id']}")
        if run["status"] != "running":
            break
        if run["turns"] and (run["turns"], run["cost_usd"]) != seen:
            seen = (run["turns"], run["cost_usd"])
            p.line(f"  turn {run['turns']}, ${run['cost_usd']:.4f}")
    p.result(run, lambda r: _show_run(r, p))


def _show_run(run: Any, p: Printer) -> None:
    p.line(
        f"run {run['id']}: {run['status'].replace('_', ' ')} after {run['turns']} turns, "
        f"${run['cost_usd']:.4f}"
    )
    if run["deck_id"]:
        p.line(f"deck {run['deck_id']}")
    if run["error"]:
        p.line(f"error: {run['error']}")
    if run["final_text"]:
        p.line()
        p.line(run["final_text"])
    if run["proposals"]:
        p.line()
        p.line("proposals:")
        for pr in run["proposals"]:
            p.line(f"  {pr['id']}  {pr['kind']} proposal  {pr['status']}")
        pending = [pr for pr in run["proposals"] if pr["status"] == "pending"]
        if pending:
            p.line(f"next: mtg-advisor proposal {pending[-1]['id']}")


def _latest_version(api: Api, deck_id: str) -> int:
    version = api.get(f"/decks/{deck_id}")["version"]
    if version is None:
        raise ApiError(f"deck {deck_id} has no saved version yet")
    return int(version)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mtg-advisor",
        description="Build a Commander deck from your card pool, through the MTG Deck Advisor API.",
    )
    parser.add_argument("--json", action="store_true", help="print the API's JSON answers")
    commands = parser.add_subparsers(dest="command", required=True)

    pool = commands.add_parser("pool", help="add, list or show card pools")
    pool_commands = pool.add_subparsers(dest="pool_command", required=True)
    add = pool_commands.add_parser("add", help="submit a pool: a list or a CSV export")
    add.add_argument("file")
    add.add_argument("--csv", action="store_true", help="the file is a CSV export")
    add.add_argument("--name", help="a name for the pool (default: the file name)")
    add.set_defaults(handler=pool_add)
    pool_commands.add_parser("list", help="every pool").set_defaults(handler=pool_list)
    show = pool_commands.add_parser("show", help="a pool and its possible commanders")
    show.add_argument("pool")
    show.set_defaults(handler=pool_show)

    for name, handler, target, help_text in (
        ("draft", draft, "pool", "draft a new deck from a pool"),
        ("refine", refine, "deck", "propose changes to a deck's saved version"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(target)
        command.add_argument("--request", default="", required=name == "refine")
        command.add_argument("--no-wait", action="store_true", help="return once it starts")
        if name == "draft":
            command.add_argument(
                "--name", help="the new deck's name (default: its commander's, once saved)"
            )
        command.set_defaults(handler=handler)

    simple: list[tuple[str, Callable[..., None], str, str]] = [
        ("run", show_run, "run", "a run's status and proposals"),
        ("proposal", proposal, "proposal", "a proposal and the deck it would make"),
        ("apply", apply, "proposal", "apply an approved proposal"),
        ("deck", deck, "deck", "a deck's versions and latest decklist"),
    ]
    for name, handler, target, help_text in simple:
        command = commands.add_parser(name, help=help_text)
        command.add_argument(target)
        command.set_defaults(handler=handler)

    command = commands.add_parser("approve", help="approve a pending proposal")
    command.add_argument("proposal")
    command.add_argument("--note")
    command.set_defaults(handler=approve)
    command = commands.add_parser("reject", help="reject a pending proposal")
    command.add_argument("proposal")
    command.add_argument("--reason", required=True)
    command.set_defaults(handler=reject)
    for name, handler, help_text in (
        ("approve-export", approve_export, "approve exporting a deck version"),
        ("export", export, "print an approved deck version as a decklist"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("deck")
        command.add_argument("--version", type=int, help="default: the latest")
        command.set_defaults(handler=handler)
    command = commands.add_parser("ask", help="ask a rules question")
    command.add_argument("question")
    command.set_defaults(handler=ask)
    return parser


if __name__ == "__main__":
    run()
