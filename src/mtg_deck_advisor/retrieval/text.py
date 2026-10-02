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

CARD_TEXT_VERSION = "cards-v1"
RULE_TEXT_VERSION = "rules-v1"

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


# The end of a sentence: a full stop, then a capital. Rule numbers ("rule
# 604.3") have no space after their dot, so they never end a sentence.
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def first_sentence(text: str) -> str:
    return SENTENCE_END.split(" ".join(text.split()), maxsplit=1)[0]


def rule_text(number: str, section: str, text: str, parent: tuple[str, str] | None = None) -> str:
    """`Section. [Parent: first sentence.] Number: text`, for one rule (see ADR 0010).

    A lettered rule often doesn't say what it's about ("Reminder text is
    ignored..." is about color identity only because of 903.4), so its
    parent's opening sentence comes first. The chunk that's stored and cited
    is still the single rule; this is only the text that gets embedded.
    """
    context = f" {parent[0]}: {first_sentence(parent[1])}" if parent else ""
    return f"{section}.{context} {number}: {' '.join(text.split())}"
