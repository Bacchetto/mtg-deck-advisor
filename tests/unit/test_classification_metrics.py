"""Scoring multi-label predictions (a set of roles per card) against gold labels."""

import pytest

from mtg_deck_advisor.evaluation.classification import score

LABELS = ["ramp", "removal", "card_draw"]


def test_perfect_predictions_score_one_everywhere() -> None:
    gold = [{"ramp"}, {"removal", "card_draw"}, set()]

    result = score(gold, gold, LABELS)

    assert result.micro.precision == result.micro.recall == result.micro.f1 == 1.0
    assert result.exact_match == 1.0


def test_per_label_precision_and_recall() -> None:
    gold = [{"ramp"}, {"ramp"}, {"removal"}, set()]
    predicted = [{"ramp"}, set(), {"removal", "ramp"}, {"ramp"}]

    result = score(gold, predicted, LABELS)

    ramp = result.per_label["ramp"]
    # Predicted ramp 3 times, right once; gold has ramp twice, found once.
    assert (ramp.true_positives, ramp.false_positives, ramp.false_negatives) == (1, 2, 1)
    assert ramp.precision == pytest.approx(1 / 3)
    assert ramp.recall == pytest.approx(1 / 2)
    assert ramp.f1 == pytest.approx(2 * (1 / 3) * (1 / 2) / (1 / 3 + 1 / 2))
    assert result.per_label["removal"].precision == 1.0
    assert result.per_label["removal"].support == 1


def test_micro_scores_pool_every_decision() -> None:
    gold = [{"ramp"}, {"removal"}]
    predicted = [{"ramp", "removal"}, set()]

    result = score(gold, predicted, LABELS)

    # 1 true positive, 1 false positive, 1 false negative overall.
    assert result.micro.precision == 0.5
    assert result.micro.recall == 0.5


def test_exact_match_needs_the_whole_set_right() -> None:
    gold = [{"ramp", "card_draw"}, {"removal"}]
    predicted = [{"ramp"}, {"removal"}]

    assert score(gold, predicted, LABELS).exact_match == 0.5


def test_a_label_never_predicted_or_present_has_no_score_rather_than_a_fake_one() -> None:
    result = score([{"ramp"}], [{"ramp"}], LABELS)

    card_draw = result.per_label["card_draw"]
    assert card_draw.support == 0
    assert card_draw.precision is None
    assert card_draw.recall is None


def test_gold_and_predictions_must_line_up() -> None:
    with pytest.raises(ValueError, match="same number"):
        score([{"ramp"}], [], LABELS)
