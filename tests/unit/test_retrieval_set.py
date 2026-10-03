"""Loading the labelled retrieval eval set: card queries and rules questions."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from mtg_deck_advisor.evaluation.retrieval_set import (
    CARD_QUERIES,
    POOL,
    RULE_QUESTIONS,
    CardQuery,
    EvalSet,
    QueryFilters,
    RuleQuestion,
    held_out_problems,
    load_card_queries,
    load_rule_questions,
    load_set,
)

CARD_HEADER = "id,kind,scope,query,filters,relevant,notes\n"
RULE_HEADER = "id,kind,question,relevant,notes\n"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "set.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_card_queries_are_loaded_with_their_filters_and_relevant_cards(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        CARD_HEADER
        + "C01,paraphrase,catalogue,taps for two colorless,,Sol Ring,\n"
        + "P01,need,pool,cheap ramp,identity=G; type=Artifact; mv_max=3,"
        + '"Sol Ring; Simic Signet",x\n',
    )

    first, second = load_card_queries(path)

    assert (first.id, first.kind, first.scope, first.relevant) == (
        "C01",
        "paraphrase",
        "catalogue",
        ["Sol Ring"],
    )
    assert first.filters == QueryFilters()
    assert second.filters == QueryFilters(identity="G", types=["Artifact"], mv_max=3)
    assert second.relevant == ["Sol Ring", "Simic Signet"]


def test_colorless_identity_is_written_as_c(tmp_path: Path) -> None:
    path = write(tmp_path, CARD_HEADER + "C01,need,catalogue,rocks,identity=C,Sol Ring,\n")

    assert load_card_queries(path)[0].filters.identity == ""


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("C01,paraphrase,catalogue,q,,,", "no relevant"),
        ("C01,guess,catalogue,q,,Sol Ring,", "kind"),
        ("C01,paraphrase,everywhere,q,,Sol Ring,", "scope"),
        ("C01,paraphrase,catalogue,q,colour=G,Sol Ring,", "filter"),
        ("C01,paraphrase,catalogue,q,mv_max=cheap,Sol Ring,", "filter"),
        ("C01,paraphrase,catalogue,q,identity=GX,Sol Ring,", "identity"),
        ("C01,paraphrase,catalogue,q,,Sol Ring; Sol Ring,", "twice"),
        ("C01,paraphrase,catalogue,,,Sol Ring,", "query"),
    ],
)
def test_a_malformed_card_query_is_rejected_with_its_line(
    tmp_path: Path, row: str, message: str
) -> None:
    path = write(tmp_path, CARD_HEADER + row + "\n")

    with pytest.raises(ValueError, match=rf"line 2.*{message}"):
        load_card_queries(path)


def test_query_ids_must_be_unique(tmp_path: Path) -> None:
    row = "C01,paraphrase,catalogue,q,,Sol Ring,\n"
    path = write(tmp_path, CARD_HEADER + row + row)

    with pytest.raises(ValueError, match=r"line 3.*C01"):
        load_card_queries(path)


def test_rules_questions_list_the_rule_numbers_that_answer_them(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        RULE_HEADER + 'R01,commander,How big is a deck?,"903.5a; 100.2a",\n',
    )

    (question,) = load_rule_questions(path)

    assert (question.id, question.kind, question.question) == (
        "R01",
        "commander",
        "How big is a deck?",
    )
    assert question.relevant == ["903.5a", "100.2a"]


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("R01,commander,q,,", "no relevant"),
        ("R01,commander,q,903,", "rule number"),
        ("R01,commander,q,903.5a.,", "rule number"),
        ("R01,trivia,q,903.5a,", "kind"),
    ],
)
def test_a_malformed_rules_question_is_rejected_with_its_line(
    tmp_path: Path, row: str, message: str
) -> None:
    path = write(tmp_path, RULE_HEADER + row + "\n")

    with pytest.raises(ValueError, match=rf"line 2.*{message}"):
        load_rule_questions(path)


def test_the_committed_eval_set_loads() -> None:
    cards = load_card_queries(CARD_QUERIES)
    rules = load_rule_questions(RULE_QUESTIONS)

    assert len(cards) >= 35 and len(rules) >= 35
    assert {query.scope for query in cards} == {"catalogue", "pool"}
    assert POOL.exists()


def eval_set(
    cards: Sequence[tuple[str, list[str]]] = (),
    rules: Sequence[tuple[str, list[str]]] = (),
    pool: Sequence[str] = (),
) -> EvalSet:
    return EvalSet(
        name="x",
        card_queries=[
            CardQuery(
                id=f"C{i}",
                kind="paraphrase",
                scope="catalogue",
                query=query,
                filters=QueryFilters(),
                relevant=relevant,
                notes="",
            )
            for i, (query, relevant) in enumerate(cards)
        ],
        rule_questions=[
            RuleQuestion(id=f"R{i}", kind="general", question=q, relevant=relevant, notes="")
            for i, (q, relevant) in enumerate(rules)
        ],
        pool=list(pool),
    )


def test_both_committed_sets_load() -> None:
    dev, test = load_set("dev"), load_set("test")

    assert len(dev.card_queries) == 44 and len(dev.rule_questions) == 45
    assert len(test.card_queries) >= 35 and len(test.rule_questions) >= 18
    assert len(test.pool) == 300


def test_the_committed_test_set_is_held_out() -> None:
    assert held_out_problems(load_set("dev"), load_set("test")) == []


def test_a_test_pool_sharing_cards_with_the_dev_pool_is_reported() -> None:
    (problem,) = held_out_problems(eval_set(pool=["Sol Ring"]), eval_set(pool=["Sol Ring"]))

    assert "pool" in problem and "Sol Ring" in problem


def test_a_test_target_labelled_in_the_dev_set_is_reported() -> None:
    dev = eval_set(cards=[("ramp", ["Arcane Signet"])])
    test = eval_set(cards=[("mana rock", ["Arcane Signet"])])

    (problem,) = held_out_problems(dev, test)
    assert "C0" in problem and "Arcane Signet" in problem


def test_a_card_the_embedding_model_was_selected_on_is_reported() -> None:
    (problem,) = held_out_problems(eval_set(), eval_set(cards=[("bolt", ["Lightning Bolt"])]))

    assert "Lightning Bolt" in problem and "ADR 0009" in problem


def test_a_rule_answering_a_dev_question_is_reported() -> None:
    dev = eval_set(rules=[("How big is a deck?", ["903.5a"])])
    test = eval_set(rules=[("Deck size?", ["903.5a"])])

    (problem,) = held_out_problems(dev, test)
    assert "R0" in problem and "903.5a" in problem


def test_a_query_repeated_from_the_dev_set_is_reported() -> None:
    dev = eval_set(cards=[("cheap ramp", ["Arcane Signet"])])
    test = eval_set(cards=[("Cheap Ramp", ["Mind Stone"])])

    (problem,) = held_out_problems(dev, test)
    assert "cheap ramp" in problem.lower()
