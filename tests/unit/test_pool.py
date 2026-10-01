"""Parsing a user's card pool: untrusted text in, entries and per-line problems out."""

import pytest

from mtg_deck_advisor.ingestion.pool import (
    MAX_ENTRIES,
    MAX_INPUT_BYTES,
    ParsedPool,
    PoolEntry,
    parse_csv,
    parse_text,
)


def entries(pool: ParsedPool) -> list[tuple[str, int]]:
    return [(entry.name, entry.quantity) for entry in pool.entries]


# --- text lists ------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("1 Sol Ring", ("Sol Ring", 1)),
        ("4 Forest", ("Forest", 4)),
        ("1x Sol Ring", ("Sol Ring", 1)),
        ("2X Sol Ring", ("Sol Ring", 2)),
        ("1 x Sol Ring", ("Sol Ring", 1)),
        ("Sol Ring", ("Sol Ring", 1)),
        ("  1   Sol Ring  ", ("Sol Ring", 1)),
        # Arena and Moxfield exports add the set and collector number.
        ("1 Sol Ring (CMR) 472", ("Sol Ring", 1)),
        ("1 Sol Ring (PLST) LRW-256", ("Sol Ring", 1)),
        ("1 Sol Ring (CMR)", ("Sol Ring", 1)),
        # ...and foil or etched markers.
        ("1 Sol Ring (CMR) 472 *F*", ("Sol Ring", 1)),
        ("1 Sol Ring *E*", ("Sol Ring", 1)),
        (
            "1 Delver of Secrets // Insectile Aberration",
            ("Delver of Secrets // Insectile Aberration", 1),
        ),
    ],
)
def test_text_line_formats(line: str, expected: tuple[str, int]) -> None:
    assert entries(parse_text(line)) == [expected]


@pytest.mark.parametrize(
    "name",
    [
        "Hazmat Suit (Used)",  # a real name ending in a parenthesis that is not a set code
        "B.F.M. (Big Furry Monster)",
        "17-Year Cicadas",  # a real name starting with digits
        "70,000 Light-Years from Home",
    ],
)
def test_real_names_that_look_like_formatting_are_kept_whole(name: str) -> None:
    assert entries(parse_text(f"1 {name}")) == [(name, 1)]
    assert entries(parse_text(name)) == [(name, 1)]


def test_a_leading_number_too_large_to_be_a_quantity_is_part_of_the_name() -> None:
    assert entries(parse_text("1996 World Champion")) == [("1996 World Champion", 1)]


def test_blank_lines_comments_and_section_headers_are_skipped() -> None:
    text = """
    // my collection
    # binder 2
    Commander
    1 Atraxa, Praetors' Voice
    Deck:
    Commander (1)
    SIDEBOARD
    Maybeboard
    1 Sol Ring
    """

    assert entries(parse_text(text)) == [("Atraxa, Praetors' Voice", 1), ("Sol Ring", 1)]


def test_entries_keep_their_line_numbers() -> None:
    pool = parse_text("// header\n\n1 Sol Ring\n2 Forest\n")

    assert pool.entries == [
        PoolEntry(name="Sol Ring", quantity=1, line=3),
        PoolEntry(name="Forest", quantity=2, line=4),
    ]


def test_a_zero_quantity_is_a_problem_and_the_line_is_skipped() -> None:
    pool = parse_text("0 Sol Ring\n1 Forest")

    assert entries(pool) == [("Forest", 1)]
    [problem] = pool.problems
    assert problem.line == 1
    assert "quantity" in problem.message


def test_a_line_with_no_name_is_a_problem() -> None:
    pool = parse_text("3 (CMR) 472")

    assert entries(pool) == []
    assert pool.problems[0].line == 1


def test_control_characters_in_a_name_are_a_problem() -> None:
    pool = parse_text("1 Sol\x00Ring\n1 Sol\x1b[31mRing")

    assert entries(pool) == []
    assert [problem.line for problem in pool.problems] == [1, 2]


def test_an_overlong_name_is_a_problem() -> None:
    pool = parse_text("1 " + "A" * 300)

    assert entries(pool) == []
    assert "long" in pool.problems[0].message


def test_problem_text_is_truncated() -> None:
    pool = parse_text("0 " + "A" * 5000)

    assert len(pool.problems[0].text) <= 100


# --- limits ----------------------------------------------------------------


def test_input_over_the_size_limit_is_rejected_whole() -> None:
    pool = parse_text("1 Sol Ring\n" * (MAX_INPUT_BYTES // 10))

    assert pool.entries == []
    [problem] = pool.problems
    assert problem.line is None
    assert "too large" in problem.message


def test_entries_beyond_the_limit_are_ignored_with_one_problem() -> None:
    pool = parse_text("1 Forest\n" * (MAX_ENTRIES + 10))

    assert len(pool.entries) == MAX_ENTRIES
    [problem] = pool.problems
    assert problem.line == MAX_ENTRIES + 1
    assert str(MAX_ENTRIES) in problem.message


# --- CSV -------------------------------------------------------------------


def test_csv_with_name_and_quantity_columns() -> None:
    csv = "Name,Set code,Quantity\nSol Ring,CMR,1\nForest,M21,4\n"

    assert entries(parse_csv(csv)) == [("Sol Ring", 1), ("Forest", 4)]


@pytest.mark.parametrize(
    "header",
    ["Name,Count", "Card Name,Qty", "card,quantity", " NAME , QUANTITY ", "card_name,amount"],
)
def test_csv_column_headers_are_matched_loosely(header: str) -> None:
    assert entries(parse_csv(f"{header}\nSol Ring,2\n")) == [("Sol Ring", 2)]


def test_csv_without_a_quantity_column_means_one_of_each() -> None:
    assert entries(parse_csv("Card Name,Edition\nSol Ring,CMR\n")) == [("Sol Ring", 1)]


def test_csv_with_a_byte_order_mark_and_quoted_commas() -> None:
    csv = '﻿Name,Quantity\n"Atraxa, Praetors\' Voice",1\n'

    assert entries(parse_csv(csv)) == [("Atraxa, Praetors' Voice", 1)]


def test_csv_with_semicolons() -> None:
    assert entries(parse_csv("Name;Quantity\nSol Ring;3\n")) == [("Sol Ring", 3)]


def test_csv_without_a_name_column_reports_one_clear_problem() -> None:
    pool = parse_csv("Set,Collector Number\nCMR,472\n")

    assert pool.entries == []
    [problem] = pool.problems
    assert problem.line == 1
    assert "name column" in problem.message


def test_csv_rows_with_bad_quantities_are_problems() -> None:
    pool = parse_csv("Name,Quantity\nSol Ring,lots\nForest,-2\nIsland,\nSwamp,2\n")

    assert entries(pool) == [("Island", 1), ("Swamp", 2)]
    assert [problem.line for problem in pool.problems] == [2, 3]


def test_csv_blank_rows_are_skipped_and_empty_names_are_problems() -> None:
    pool = parse_csv("Name,Quantity\n\n,3\nSol Ring,1\n")

    assert entries(pool) == [("Sol Ring", 1)]
    assert [problem.line for problem in pool.problems] == [3]


def test_empty_csv_is_a_problem() -> None:
    pool = parse_csv("")

    assert pool.entries == []
    assert pool.problems[0].message
