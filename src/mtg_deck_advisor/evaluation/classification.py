"""Scoring multi-label classification, such as card roles, against gold labels.

Each item has a set of labels (a card can be both ramp and mana_fixing, or
neither), so scores are computed per label and pooled ("micro"), plus exact
match, which counts an item right only if its whole set of labels is right.
"""

from collections.abc import Sequence, Set

from pydantic import BaseModel


class LabelScore(BaseModel):
    true_positives: int
    false_positives: int
    false_negatives: int
    # How many items truly have this label.
    support: int
    # None when undefined (nothing predicted, or nothing to find): a missing
    # score is more honest than a 0 or 1 that nothing earned.
    precision: float | None
    recall: float | None
    f1: float | None


class ClassificationScore(BaseModel):
    per_label: dict[str, LabelScore]
    micro: LabelScore
    exact_match: float


def _label_score(tp: int, fp: int, fn: int) -> LabelScore:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return LabelScore(
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        support=tp + fn,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def score(
    gold: Sequence[Set[str]], predicted: Sequence[Set[str]], labels: Sequence[str]
) -> ClassificationScore:
    """Score predictions against gold labels, item by item, in the same order."""
    if len(gold) != len(predicted):
        raise ValueError(
            f"gold and predictions must have the same number of items "
            f"({len(gold)} against {len(predicted)})"
        )
    per_label = {}
    totals = [0, 0, 0]
    for label in labels:
        tp = sum(label in g and label in p for g, p in zip(gold, predicted, strict=True))
        fp = sum(label not in g and label in p for g, p in zip(gold, predicted, strict=True))
        fn = sum(label in g and label not in p for g, p in zip(gold, predicted, strict=True))
        per_label[label] = _label_score(tp, fp, fn)
        totals = [totals[0] + tp, totals[1] + fp, totals[2] + fn]
    exact = sum(set(g) == set(p) for g, p in zip(gold, predicted, strict=True))
    return ClassificationScore(
        per_label=per_label,
        micro=_label_score(*totals),
        exact_match=exact / len(gold) if gold else 0.0,
    )
