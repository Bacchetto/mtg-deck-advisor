"""The text that gets embedded for each card and each rule.

Rules text is full of symbols: `{T}: Add {C}{C}.` No embedding model connects
that to "taps for two colorless mana", and in the model selection Sol Ring
ranked 500 to 1,000 for that query in every model. Writing the symbols out as
words put it in the top 10 for all of them (ADR 0009). The text is what that
selection measured: name, type line and rules text. Adding the mana cost
looked useful but wasn't measured, and a spot check showed it pulling cards
away from descriptions of their effects; the retrieval eval decides it.

Rules are chunked one per rule number, as ADR 0010 explains.

Change CARD_TEXT_VERSION or RULE_TEXT_VERSION whenever the text changes, so stored embeddings are
recognised as stale and redone.
"""

import re
from typing import Literal

CARD_TEXT_VERSION = "cards-v1"
RULE_TEXT_VERSION = "rules-v2"
# Summaries are embedded as written (retrieval.summaries).
SUMMARY_TEXT_VERSION = "summaries-v1"

COLOR_WORDS = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green"}
SYMBOL_WORDS = {
    "T": "tap",
    "Q": "untap",
    "C": "one colorless mana",
    "S": "one snow mana",
    "X": "X mana",
    "E": "one energy",
    **{symbol: f"one {color} mana" for symbol, color in COLOR_WORDS.items()},
}
NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
SYMBOL = re.compile(r"\{([^}]+)\}")


def _generic(amount: str) -> str:
    number = int(amount)
    return NUMBER_WORDS[number] if number < len(NUMBER_WORDS) else amount


def _symbol_words(symbol: str) -> str:
    if symbol.isdigit():
        return f"{_generic(symbol)} generic mana"
    if "/" not in symbol:
        return SYMBOL_WORDS.get(symbol, symbol)
    parts = symbol.split("/")
    if parts[-1] == "P":  # Phyrexian: payable with the color or 2 life
        colors = " or ".join(COLOR_WORDS.get(part, part) for part in parts[:-1])
        return f"one {colors} Phyrexian mana"
    if parts[0].isdigit():  # {2/W}: two generic, or one of the color
        return f"{_generic(parts[0])} generic or one {COLOR_WORDS.get(parts[1], parts[1])} mana"
    return f"one {' or '.join(COLOR_WORDS.get(part, part) for part in parts)} mana"


def expand_symbols(text: str) -> str:
    """Text with mana, tap and other symbols written out as words."""
    expanded = SYMBOL.sub(lambda match: f" {_symbol_words(match[1])} ", text)
    expanded = re.sub(r"[ \t]+", " ", expanded)
    return re.sub(r" +([.,:;])", r"\1", expanded).strip()


def card_text(name: str, type_line: str, oracle_text: str) -> str:
    """`Name. Type line. Rules text`, with symbols as words and abilities separated by " / "."""
    text = f"{name}. {type_line}."
    if oracle_text.strip():
        lines = (expand_symbols(line) for line in oracle_text.splitlines())
        text += " " + " / ".join(line for line in lines if line)
    return text


def rule_text(text: str) -> str:
    """The text embedded for one rule: its own text, examples included, on one line.

    The section heading and the parent rule's first sentence were added as
    context at first; the retrieval eval measured them as a loss (recall@10
    0.89 with them, 0.97 without), so each rule is embedded as itself.
    See ADR 0010.
    """
    return " ".join(text.split())


# Qwen3 embedding models expect a query to carry a one-line task instruction
# (documents carry none). The cards one is what ADR 0009 measured.
QUERY_INSTRUCTIONS = {
    "cards": (
        "Given a description of a card's effect, retrieve the Magic: The Gathering card that has it"
    ),
    "rules": (
        "Given a question about the Magic: The Gathering rules, retrieve the rule that answers it"
    ),
}


def query_text(model: str, kind: Literal["cards", "rules"], query: str) -> str:
    """A search query in the form the embedding model expects."""
    if model.startswith("qwen3-embedding"):
        return f"Instruct: {QUERY_INSTRUCTIONS[kind]}\nQuery: {query}"
    return query
