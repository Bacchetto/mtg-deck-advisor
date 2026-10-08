"""Rules for variants other than Commander, kept out of searches unless asked for (#138).

The Comprehensive Rules cover many variants, and some share Commander's
words: Brawl (903.12) and Commander Draft (903.13) are written as changes to
Commander's own rules, and the team variants talk about commanders and
commander damage too. A search for a Commander question returned them, and
answers cited them: in the rules Q&A baseline, 10 of Sonnet's 11 failures did,
and one (R11) said a planeswalker can be a commander, which is Brawl's rule.

So rules search leaves a variant's sections out unless the question names
the variant. The general multiplayer rules (800-804, 806: range of
influence, attacking several players, Free-for-All) stay in, because a
Commander game uses them.
"""

import re

# A variant's names, as a question would use them, and the sections that are
# its alone. Shared team turns (805) belong to the team variants that use them.
VARIANTS: dict[str, tuple[str, ...]] = {
    "brawl": ("903.12",),
    "commander draft": ("903.13",),
    "two-headed giant": ("810", "805"),
    "grand melee": ("807",),
    "team vs. team": ("808", "805"),
    "team vs team": ("808", "805"),
    "emperor": ("809",),
    "alternating teams": ("811", "805"),
    "shared team turns": ("805",),
    "planechase": ("901",),
    "vanguard": ("902",),
    "archenemy": ("904", "805"),
    "conspiracy": ("905",),
}
SECTIONS: tuple[str, ...] = tuple(sorted({s for sections in VARIANTS.values() for s in sections}))


def _pattern(sections: tuple[str, ...] | list[str]) -> str:
    """A regular expression for rule numbers in these sections, for Python and Postgres.

    A section's rules are the section itself, its sub-rules ("903.12c") and its
    numbered rules ("805.8"), but not a longer number ("903.120").
    """
    prefixes = "|".join(re.escape(section) for section in sections)
    return rf"^({prefixes})([a-z.]|$)"


VARIANT_RULE = re.compile(_pattern(SECTIONS))


def is_variant_rule(number: str) -> bool:
    """Whether a rule belongs to a variant other than Commander."""
    return VARIANT_RULE.match(number) is not None


def excluded_variants(question: str) -> list[str]:
    """The variant sections to leave out of a search: all but those the question names."""
    text = question.lower()
    named = {s for name, sections in VARIANTS.items() if name in text for s in sections}
    return [section for section in SECTIONS if section not in named]


def excluded_pattern(question: str) -> str | None:
    """A Postgres regular expression for the rules to leave out, or None to keep all."""
    excluded = excluded_variants(question)
    return _pattern(excluded) if excluded else None
