"""Parsing the Comprehensive Rules text file into individual, citable rules.

The file is plain text: a header with the effective date, an introduction, a
table of contents, the rules, a glossary and credits. Each rule starts on its
own line with its number ("100.1." or "100.1a"), and its examples follow on
lines of their own ("Example: ..."). Splitting on rule numbers makes each
stored rule one unit that an answer can cite by number (RAG-2, RAG-3).
"""

import re
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

# "100.1. text", "100.1a text", "704.5aa text": a section number, a rule
# number, optional letters, and a trailing period on unlettered rules only.
RULE = re.compile(r"^(?P<number>(?P<base>\d{3}\.\d+)(?P<letters>[a-z]*))\.?\s+(?P<text>\S.*)$")
# "903. Commander": a section heading, in the contents and again in the body.
SECTION = re.compile(r"^(?P<number>\d{3})\.\s+(?P<title>\S.*)$")
# "9. Casual Variants": a chapter heading.
CHAPTER = re.compile(r"^\d\.\s+\S")
EFFECTIVE_DATE = re.compile(r"effective as of (?P<date>[A-Z][a-z]+ \d{1,2}, \d{4})")


class RuleRecord(BaseModel):
    """One rule, as stored in the `rules` table."""

    model_config = ConfigDict(frozen=True)

    number: str
    # The rule a lettered rule belongs to: "903.4" for "903.4a".
    parent: str | None
    # Its section heading, such as "903. Commander", for context in citations.
    section: str
    # The rule's text, with any examples on the lines that follow.
    text: str


class ParsedRules(BaseModel):
    effective_date: date
    rules: list[RuleRecord]


def parse_rules(text: str) -> ParsedRules:
    """Every numbered rule in the file, in order, with the file's effective date."""
    found = EFFECTIVE_DATE.search(text)
    if found is None:
        raise ValueError("no effective date found: expected 'effective as of <Month D, YYYY>'")
    effective_date = datetime.strptime(found["date"], "%B %d, %Y").date()

    titles: dict[str, str] = {}
    # (number, base, letters, text lines) for each rule, in order.
    parsed: list[tuple[str, str, str, list[str]]] = []
    current: list[str] | None = None

    for raw_line in text.splitlines():
        # strip() also removes the non-breaking spaces the file uses on
        # otherwise blank lines.
        line = raw_line.strip()
        if not line:
            continue
        if parsed and line == "Glossary":
            break  # the glossary and credits follow the last rule
        if rule := RULE.match(line):
            current = [rule["text"]]
            parsed.append((rule["number"], rule["base"], rule["letters"], current))
        elif section := SECTION.match(line):
            titles[section["number"]] = section["title"]
            current = None
        elif CHAPTER.match(line):
            current = None
        elif current is not None:
            current.append(line)  # an example, or a continuation of the rule

    if not parsed:
        raise ValueError("no numbered rules found")

    rules: list[RuleRecord] = []
    seen: set[str] = set()
    for number, base, letters, lines in parsed:
        if number in seen:
            raise ValueError(f"rule {number} appears twice")
        seen.add(number)
        section_number = number[:3]
        if section_number not in titles:
            raise ValueError(f"rule {number} has no section heading {section_number}")
        rules.append(
            RuleRecord(
                number=number,
                parent=base if letters else None,
                section=f"{section_number}. {titles[section_number]}",
                text="\n".join(lines),
            )
        )
    return ParsedRules(effective_date=effective_date, rules=rules)
