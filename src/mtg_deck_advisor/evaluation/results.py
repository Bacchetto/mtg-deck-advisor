"""Eval runs and their per-case results, as saved under evals/runs/ (#120, EVL-4).

Kept apart from the runner, with nothing but Pydantic and the standard
library, so tools that only read saved runs (hand grading, reports) load
quickly: the runner brings in the agent, the model SDK and search.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

RUNS = Path("evals/runs")

# The search modes a variant can name; the same as retrieval.search.SearchMode
# (a test keeps them equal), written here so reading runs doesn't load search.
type SearchMode = Literal["vector", "keyword", "hybrid"]


class Variant(BaseModel):
    name: str
    model: str
    # A key of PROMPT_VARIANTS.
    prompt: str = "default"
    card_search: SearchMode = "hybrid"
    # Rerank card searches with the local reranker (when one is configured).
    rerank: bool = True
    rules_search: SearchMode = "vector"


class ToolCounts(BaseModel):
    ok: int = 0
    error: int = 0
    rejected: int = 0


class CaseResult(BaseModel):
    case_id: str
    # The run's status ("completed", "cost_capped"...), or "skipped" (over
    # budget) or "error" (the case itself failed).
    status: str
    success: bool
    # The agent's cost; grading (a model judging the result) is counted apart.
    cost_usd: float = 0.0
    grading_cost_usd: float = 0.0
    latency_s: float = 0.0
    turns: int = 0
    tools: ToolCounts = ToolCounts()
    run_id: UUID | None = None
    # What the suite wants to keep: the answer, the proposal, the citations...
    details: dict[str, Any] = {}
    # Grades added later (rubric scores, hand checks).
    scores: dict[str, float] = {}
    error: str | None = None


class EvalRun(BaseModel):
    suite: str
    variant: Variant
    started_at: datetime
    finished_at: datetime | None = None
    # The commit the code was at, so a result can be traced to its code.
    commit: str | None = None
    budget_usd: float
    results: list[CaseResult] = []

    @property
    def total_cost_usd(self) -> float:
        """What the agent cost, without grading."""
        return sum(result.cost_usd for result in self.results)

    @property
    def spent_usd(self) -> float:
        """Everything this run paid for: the agent and the grading. The budget is checked on it."""
        return sum(result.cost_usd + result.grading_cost_usd for result in self.results)

    def save(self, directory: Path = RUNS) -> Path:
        stamp = self.started_at.astimezone(UTC).strftime("%Y-%m-%dT%H%M%S")
        path = directory / self.suite / f"{stamp}-{self.variant.name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "EvalRun":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
