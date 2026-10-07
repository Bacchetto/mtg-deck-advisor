"""The deck quality rubric's criteria (evals/rubrics/deck_quality.md), in its order.

Their own module so the hand-grading tool can name them without loading the
deck task harness.
"""

# "fit" (fit to the request) is skipped when a task has no request.
CRITERIA = ("plan", "fit", "mana", "ramp", "draw", "interaction")
LABELS = {
    "plan": "plan coherence",
    "fit": "fit to the request",
    "mana": "mana base",
    "ramp": "ramp",
    "draw": "card draw",
    "interaction": "interaction",
}
