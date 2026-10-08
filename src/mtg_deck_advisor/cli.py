"""`mtg-advisor`: a thin command-line client of the HTTP API.

    mtg-advisor build [POOL_FILE | POOL] [--deck DECK] [--request TEXT] [--name NAME]
    mtg-advisor pool add pool.txt [--csv] [--name NAME]
    mtg-advisor pool list | pool show POOL
    mtg-advisor draft POOL [--request TEXT] [--name NAME] [--no-wait]
    mtg-advisor refine DECK --request TEXT [--no-wait]
    mtg-advisor run RUN
    mtg-advisor proposal PROPOSAL
    mtg-advisor approve PROPOSAL [--note TEXT] | reject PROPOSAL --reason TEXT
    mtg-advisor apply PROPOSAL
    mtg-advisor decks [--pool POOL] [--archived]
    mtg-advisor deck DECK | rename DECK NAME | archive DECK | unarchive DECK
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

    def patch(self, path: str, body: dict[str, Any]) -> Any:
        return self._send("PATCH", path, body).json()

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
    ask: Callable[[str], str] = input,
) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse has printed the usage problem
        return exc.code if isinstance(exc.code, int) else 2
    args.ask = ask
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
        _show_proposal(pr, p)
        if pr["status"] == "pending":
            p.line()
            p.line(f"next: mtg-advisor approve {pr['id']}  (or reject {pr['id']} --reason ...)")

    p.result(api.get(f"/proposals/{args.proposal}"), show)


def _show_proposal(pr: Any, p: Printer) -> None:
    """A proposal: its rationale, citations, problems, changes and the deck it would make."""
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


def decks(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    query = f"?include_archived={str(args.archived).lower()}"
    if args.pool:
        query += f"&pool_id={args.pool}"

    def show(listing: Any) -> None:
        if not listing:
            p.line(
                "no decks" + ("" if args.archived else " (archived ones are hidden: --archived)")
            )
        for d in listing:
            saved = f"version {d['version']}" if d["version"] else "nothing saved yet"
            p.line(f"{d['id']}  {d['name']}: {saved}" + (" (archived)" if d["archived"] else ""))

    p.result(api.get(f"/decks{query}"), show)


def archive(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    action = args.action
    result = api.post(f"/decks/{args.deck}/{action}")
    p.result(result, lambda r: p.line(f"{r['name']!r} is {action}d"))


def rename(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    renamed = api.patch(f"/decks/{args.deck}", {"name": args.name})
    p.result(renamed, lambda r: p.line(f"{r['previous_name']!r} is now {r['name']!r}"))


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


# --- build: the whole process in one session -----------------------------------------------

type Ask = Callable[[str], str]

MENU = "[a]pprove  [r]eject  [c]hange  [e]xport  [q]uit"
REQUEST_PROMPT = (
    "What should the deck be built around? Name a commander or a theme "
    '(e.g. "Adeline" or "tokens"), ? to see the commanders your pool can use, '
    "or Enter to let the agent choose: "
)


def build(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep) -> None:
    """Draft a deck, then decide, change and export it until the user quits.

    Every step is the API's, as with the single commands; approving and
    exporting happen only when the user types them.
    """
    p = Printer(p.out, as_json=False)  # an interactive session, not something to parse
    ask: Ask = args.ask
    try:
        deck_id = args.deck or _start_build(api, args, p, sleep, ask)
    except EOFError:
        p.line()
        return
    _build_loop(api, deck_id, p, sleep, ask)


def _start_build(api: Api, args: argparse.Namespace, p: Printer, sleep: Sleep, ask: Ask) -> str:
    pool_id = _choose_pool(api, args, p, ask)
    request = args.request
    while request is None:
        request = ask(REQUEST_PROMPT).strip()
        if request == "?":
            _list_commanders(api, pool_id, p)
            request = None
    body = {"request": request} | ({"name": args.name} if args.name else {})
    started = api.post(f"/pools/{pool_id}/drafts", body)
    _wait(api, started, p, sleep)
    return str(started["deck_id"])


def _list_commanders(api: Api, pool_id: str, p: Printer) -> None:
    commanders = api.get(f"/pools/{pool_id}")["commanders"]
    if not commanders:
        p.line("no card in this pool can be a commander, so a deck can't be drafted from it")
        return
    p.line("commanders your pool can use:")
    for commander in commanders:
        p.line(f"  {commander['name']} ({commander['color_identity'] or 'colorless'})")


def _choose_pool(api: Api, args: argparse.Namespace, p: Printer, ask: Ask) -> str:
    """The pool to build from: a file to submit, a pool ID, or one chosen from the list."""
    if args.pool and Path(args.pool).is_file():
        path = Path(args.pool)
        csv = args.csv or path.suffix.lower() == ".csv"
        body = {
            "name": path.stem,
            "format": "csv" if csv else "text",
            "content": path.read_text(encoding="utf-8"),
        }
        created = api.post("/pools", body)
        p.line(f"pool {path.stem!r}: {created['cards']} cards ({created['distinct']} different)")
        if created["unresolved"]:
            p.line(f"unresolved, left out: {', '.join(created['unresolved'])}")
        return str(created["pool_id"])
    if args.pool:
        return str(args.pool)
    pools = api.get("/pools")
    if not pools:
        raise ApiError("no pools yet: start with a pool file, `mtg-advisor build pool.txt`")
    for n, pool in enumerate(pools, 1):
        p.line(f"{n}) {pool['name']}: {pool['cards']} cards")
    while True:
        choice = ask("Which pool? ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(pools):
            return str(pools[int(choice) - 1]["id"])
        p.line(f"type a number from 1 to {len(pools)}")


def _build_loop(api: Api, deck_id: str, p: Printer, sleep: Sleep, ask: Ask) -> None:
    shown: str | None = None
    while True:
        deck = api.get(f"/decks/{deck_id}")
        current = _open_proposal(api, deck)
        if current and current["id"] != shown:
            shown = current["id"]
            p.line()
            _show_proposal(api.get(f"/proposals/{current['id']}"), p)
        elif current is None and shown != "":
            shown = ""
            saved = f"version {deck['version']}" if deck["version"] else "nothing saved yet"
            p.line(f"{deck['name']}: {saved}, and no proposal waiting")
        p.line()
        p.line(MENU)
        try:
            choice = ask("> ").strip().lower()[:1]
            if choice == "q":
                break
            _build_step(api, deck, current, choice, p, sleep, ask)
        except EOFError:
            break
        except ApiError as exc:
            p.line(f"refused: {exc}")
    deck = api.get(f"/decks/{deck_id}")
    saved = f"version {deck['version']}" if deck["version"] else "nothing saved yet"
    p.line()
    p.line(f"{deck['name']} ({deck_id}): {saved}")
    p.line(f"continue with: mtg-advisor build --deck {deck_id}")


def _build_step(
    api: Api,
    deck: Any,
    current: Any | None,
    choice: str,
    p: Printer,
    sleep: Sleep,
    ask: Ask,
) -> None:
    deck_id = deck["id"]
    if choice == "a":
        if current is None:
            p.line("there's no proposal to approve; [c]hange to ask for one")
            return
        if current["status"] == "pending":
            api.post(f"/proposals/{current['id']}/approval")
        applied = api.post(f"/proposals/{current['id']}/application")
        p.line(f"approved and saved as version {applied['version']}")
    elif choice == "r":
        if current is None:
            p.line("there's no proposal to reject")
            return
        reason = ask("What's wrong with it? It's passed on to a new attempt (Enter to skip): ")
        _reject(api, current, reason.strip() or "Rejected in mtg-advisor build.")
        if reason.strip():
            _again(api, deck, reason.strip(), p, sleep)
    elif choice == "c":
        change = ask("What should change? ").strip()
        if not change:
            return
        if current is not None and current["status"] == "pending":
            _reject(api, current, f"Asked for a change instead: {change}")
        _again(api, deck, change, p, sleep)
    elif choice == "e":
        if deck["version"] is None:
            p.line("nothing saved to export yet: approve a proposal first")
            return
        default = _filename(deck["name"])
        path = Path(ask(f"Save the decklist to [{default}]: ").strip() or default)
        version = deck["version"]
        api.post(f"/decks/{deck_id}/versions/{version}/export-approval")
        path.write_text(api.text(f"/decks/{deck_id}/versions/{version}/export") + "\n", "utf-8")
        p.line(f"exported version {version} to {path}")
    else:
        p.line("type a, r, c, e or q")


def _open_proposal(api: Api, deck: Any) -> Any | None:
    """The newest proposal still waiting on the user for the deck as it is now."""
    proposals = api.get(f"/decks/{deck['id']}/proposals")
    waiting = [
        pr
        for pr in proposals
        if pr["status"] in ("pending", "approved") and pr["base_version"] == deck["version"]
    ]
    return waiting[-1] if waiting else None


def _reject(api: Api, proposal: Any, reason: str) -> None:
    if proposal["status"] == "pending":
        api.post(f"/proposals/{proposal['id']}/rejection", {"reason": reason})


def _again(api: Api, deck: Any, request: str, p: Printer, sleep: Sleep) -> None:
    """Ask the agent again: refine a saved deck, or draft again into an unsaved one."""
    if deck["version"] is None:
        started = api.post(f"/decks/{deck['id']}/drafts", {"request": request})
    else:
        started = api.post(f"/decks/{deck['id']}/refinements", {"request": request})
    _wait(api, started, p, sleep)


def _wait(api: Api, started: Any, p: Printer, sleep: Sleep) -> None:
    p.line("the agent is working...")
    run = _poll(api, started["run_id"], p, sleep)
    p.line(f"done after {run['turns']} turns, ${run['cost_usd']:.4f}")
    if run["error"]:
        p.line(f"{_problem(run)}: {run['error']}")
    if run["final_text"]:
        p.line()
        p.line(run["final_text"])


def _filename(name: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    return f"{'-'.join(filter(None, slug.split('-'))) or 'deck'}.txt"


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
    run = _poll(api, started["run_id"], p, sleep)
    p.result(run, lambda r: _show_run(r, p))


def _poll(api: Api, run_id: str, p: Printer, sleep: Sleep) -> Any:
    """Wait for a run to end, showing each turn and the cost so far; return the run.

    Turns that ended between two polls are shown together ("turns 2-4"), so
    every turn is accounted for however fast the run goes.
    """
    shown = 0
    while True:
        sleep(POLL_SECONDS)
        run = api.get(f"/runs/{run_id}")
        if run["turns"] > shown:
            first, last = shown + 1, run["turns"]
            turns = f"turn {last}" if first == last else f"turns {first}-{last}"
            p.line(f"  {turns}, ${run['cost_usd']:.4f}")
            shown = last
        if run["status"] != "running":
            return run


def _problem(run: Any) -> str:
    """How a run's error is labelled: a refusal isn't a failure of the app."""
    return "refused" if run["status"] == "refused" else "error"


def _show_run(run: Any, p: Printer) -> None:
    p.line(
        f"run {run['id']}: {run['status'].replace('_', ' ')} after {run['turns']} turns, "
        f"${run['cost_usd']:.4f}"
    )
    if run["deck_id"]:
        p.line(f"deck {run['deck_id']}")
    if run["error"]:
        p.line(f"{_problem(run)}: {run['error']}")
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

    command = commands.add_parser("decks", help="list decks (archived ones only with --archived)")
    command.add_argument("--pool", help="only this pool's decks")
    command.add_argument("--archived", action="store_true", help="include archived decks")
    command.set_defaults(handler=decks)
    for name, help_text in (
        ("archive", "hide a deck from lists, keeping its history"),
        ("unarchive", "bring an archived deck back"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("deck")
        command.set_defaults(handler=archive, action=name)
    command = commands.add_parser("rename", help="rename a deck")
    command.add_argument("deck")
    command.add_argument("name")
    command.set_defaults(handler=rename)
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
    command = commands.add_parser(
        "build", help="build a deck interactively: draft, decide, change and export"
    )
    command.add_argument("pool", nargs="?", help="a pool file to submit, or a pool ID")
    command.add_argument("--csv", action="store_true", help="the pool file is a CSV export")
    command.add_argument("--deck", help="continue this deck instead of drafting a new one")
    command.add_argument("--request", help="what you want (asked for if not given)")
    command.add_argument("--name", help="the new deck's name (default: its commander's)")
    command.set_defaults(handler=build)
    command = commands.add_parser("ask", help="ask a rules question")
    command.add_argument("question")
    command.set_defaults(handler=ask)
    return parser


if __name__ == "__main__":
    run()
