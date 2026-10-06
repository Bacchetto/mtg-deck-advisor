"""Parsing a user's card pool from a pasted text list or a CSV export (ING-1).

Pool input is untrusted (GRD-3): anything a user pastes or uploads. The
parsers never raise on bad input. They return the entries they could read and
a list of problems, each tied to a line, so a user can fix their input and a
bad line never takes the rest of the pool down with it. Size limits bound the
work any one input can cause.

Parsing only reads names; turning names into cards is resolution's job.
"""

import csv
import io
import re
import unicodedata

from pydantic import BaseModel, ConfigDict

MAX_INPUT_BYTES = 1_000_000
MAX_ENTRIES = 5_000
MAX_QUANTITY = 999
# The longest real card name is 141 characters.
MAX_NAME_LENGTH = 200
# How much of a bad line a problem quotes back.
MAX_PROBLEM_TEXT = 100

# "4 Forest" or "4x Forest". The x must touch the number: "1 x ..." is not
# accepted, because a real card is named "X" ("1 X (CMR) 472").
QUANTITY = re.compile(r"^(?P<quantity>\d+)[xX]?\s+(?P<rest>.*)$")
# Trailing export decorations: foil and etched markers ("*F*", "*E*"), then a
# set code in uppercase ("(CMR)", "(PLST)") with an optional collector number
# ("472", "LRW-256"). Set codes are matched in uppercase only, so a real name
# ending in a parenthesis, like "Hazmat Suit (Used)", is left alone.
FINISH_MARKERS = re.compile(r"(?:\s+\*[A-Z]+\*)+$")
# At the start too, so a line holding only "(CMR) 472" ends up with no name.
SET_AND_NUMBER = re.compile(r"(?:^|\s+)\([A-Z0-9]{2,6}\)(?:\s+\S+)?$")
# Section headers in exported lists: "Commander", "Deck:", "Sideboard (15)".
SECTION_HEADER = re.compile(
    r"^(commander|companion|deck|main|mainboard|sideboard|maybeboard|considering|tokens)"
    r"\s*(\(\d+\))?:?$",
    re.IGNORECASE,
)

# Recognised column headers, compared loosely (`_header_key`). "product name"
# and the two quantity columns are TCGplayer's app export.
NAME_HEADERS = ("name", "card name", "card", "cardname", "product name")
# In order of preference: a row's quantity is the first of these it fills in.
# A TCGplayer export can leave "total quantity" empty and count the cards in
# "add to quantity".
QUANTITY_HEADERS = ("quantity", "count", "qty", "amount", "total quantity", "add to quantity")
# Which game a row is for, in exports covering several (TCGplayer's).
GAME_HEADER = "product line"
MAGIC = "magic: the gathering"


class PoolEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    quantity: int
    # 1-based line of the input the entry came from, for problem reports.
    line: int


class PoolProblem(BaseModel):
    model_config = ConfigDict(frozen=True)

    # None for a problem with the input as a whole.
    line: int | None
    message: str
    # The start of the offending line, for showing back to the user.
    text: str = ""


class ParsedPool(BaseModel):
    entries: list[PoolEntry]
    problems: list[PoolProblem]


class _Collector:
    """Gathers entries and problems, enforcing the entry limit."""

    def __init__(self) -> None:
        self.entries: list[PoolEntry] = []
        self.problems: list[PoolProblem] = []
        self.full = False

    def problem(self, line: int | None, message: str, text: str = "") -> None:
        self.problems.append(PoolProblem(line=line, message=message, text=text[:MAX_PROBLEM_TEXT]))

    def add(self, name: str, quantity: int, line: int, raw: str) -> None:
        if self.full:
            return
        if len(self.entries) == MAX_ENTRIES:
            self.full = True
            self.problem(
                line, f"more than {MAX_ENTRIES} entries; this line and the rest are ignored"
            )
            return
        if error := _name_error(name):
            self.problem(line, error, raw)
            return
        if not 1 <= quantity <= MAX_QUANTITY:
            self.problem(line, f"quantity must be between 1 and {MAX_QUANTITY}", raw)
            return
        self.entries.append(PoolEntry(name=name, quantity=quantity, line=line))

    def result(self) -> ParsedPool:
        return ParsedPool(entries=self.entries, problems=self.problems)


def _name_error(name: str) -> str | None:
    if not name:
        return "no card name"
    if len(name) > MAX_NAME_LENGTH:
        return f"name too long (over {MAX_NAME_LENGTH} characters)"
    if any(unicodedata.category(ch) == "Cc" for ch in name):
        return "name contains control characters"
    return None


def _too_large(text: str) -> ParsedPool | None:
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        collector = _Collector()
        collector.problem(None, f"input too large (over {MAX_INPUT_BYTES:,} bytes)")
        return collector.result()
    return None


def parse_text(text: str) -> ParsedPool:
    """Entries from a list with one card per line, such as "1 Sol Ring (CMR) 472"."""
    if rejected := _too_large(text):
        return rejected

    collector = _Collector()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("//", "#")) or SECTION_HEADER.match(line):
            continue

        quantity, name = 1, line
        if (match := QUANTITY.match(line)) and int(match["quantity"]) <= MAX_QUANTITY:
            # A larger leading number is part of the name ("1996 World Champion").
            quantity, name = int(match["quantity"]), match["rest"]
        name = SET_AND_NUMBER.sub("", FINISH_MARKERS.sub("", name)).strip()

        collector.add(name, quantity, number, raw)
        if collector.full:
            break
    return collector.result()


def _header_key(header: str) -> str:
    return re.sub(r"[\s_]+", " ", header.strip().casefold())


def _delimiter(header_line: str) -> str:
    """Comma, unless the header uses semicolons or tabs instead, as some locales do."""
    return max((",", ";", "\t"), key=header_line.count)


def _cell(row: list[str], column: int | None) -> str:
    """A row's value in a column, or "" if it has no such column."""
    return row[column].strip() if column is not None and column < len(row) else ""


def parse_csv(text: str) -> ParsedPool:
    """Entries from a CSV export with a recognisable name column and optional quantity."""
    if rejected := _too_large(text):
        return rejected

    collector = _Collector()
    text = text.removeprefix("﻿")
    if not text.strip():
        collector.problem(None, "the file is empty")
        return collector.result()

    reader = csv.reader(io.StringIO(text), delimiter=_delimiter(text.splitlines()[0]))
    try:
        header = next(reader)
        keys = [_header_key(cell) for cell in header]
        name_column = next((i for i, key in enumerate(keys) if key in NAME_HEADERS), None)
        quantity_columns = [keys.index(key) for key in QUANTITY_HEADERS if key in keys]
        game_column = keys.index(GAME_HEADER) if GAME_HEADER in keys else None
        if name_column is None:
            collector.problem(
                1,
                "no name column; expected a header such as "
                + ", ".join(f'"{h}"' for h in sorted(NAME_HEADERS)),
                ",".join(header),
            )
            return collector.result()

        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            raw = ",".join(row)
            game = _cell(row, game_column)
            if game and game.casefold() != MAGIC:
                collector.problem(
                    reader.line_num, f"not a Magic: The Gathering card ({game}); skipped", raw
                )
                continue
            name = _cell(row, name_column)
            quantity_text = next(
                (text for column in quantity_columns if (text := _cell(row, column))), ""
            )
            if quantity_text and not quantity_text.isdigit():
                collector.problem(reader.line_num, "quantity is not a whole number", raw)
                continue
            collector.add(name, int(quantity_text or "1"), reader.line_num, raw)
            if collector.full:
                break
    except csv.Error as exc:
        collector.problem(reader.line_num, f"unreadable CSV: {exc}")
    return collector.result()
