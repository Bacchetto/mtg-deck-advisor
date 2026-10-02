"""On-demand role tagging for a pool, against a real database and a deterministic fake model."""

import json
import re
import threading
from pathlib import Path
from uuid import UUID

import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.ingestion.card_ingestion import apply_cards
from mtg_deck_advisor.ingestion.cards import normalise
from mtg_deck_advisor.llm.client import BudgetExceededError, ModelClient
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.roles import PROMPT_VERSION, CardRoles, load_roles, save_roles
from mtg_deck_advisor.llm.tagging import roles_for
from mtg_deck_advisor.llm.types import ProviderRequest, ProviderResponse, Usage
from mtg_deck_advisor.observability.tracing import traced

FIXTURES = Path(__file__).parent.parent / "fixtures" / "scryfall"
ROLES_BY_NAME = {"Sol Ring": ["ramp"], "Mox Jet": ["ramp"], "Lim-Dûl's Cohort": []}


class TaggerModel:
    """A fake model that answers per card name, like a real one would.

    Batches run concurrently, so a fake that hands out scripted replies in
    order would give answers to the wrong batches; this one reads each request.
    """

    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.tagged: list[str] = []
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "fake"

    @property
    def bills(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, model: str) -> ProviderResponse:
        listing = request.messages[0].content
        names = [m[1] for m in re.finditer(r"^\d+\. (.+?) \|", listing, re.MULTILINE)]
        if self.fail_on in names:
            raise ConnectionError("the model is unavailable")
        with self._lock:
            self.tagged += names
        cards = [
            {"index": i, "roles": ROLES_BY_NAME.get(name, ["removal"]), "reason": "test"}
            for i, name in enumerate(names, 1)
        ]
        return ProviderResponse(
            text=json.dumps({"cards": cards}),
            stop_reason="end_turn",
            usage=Usage(input_tokens=400, output_tokens=40 * len(names)),
            model="claude-opus-5-5",
        )


@pytest.fixture
def loaded(settings: Settings) -> Settings:
    upgrade(settings)
    lines = [
        line
        for name in ("oracle_cards_sample.jsonl", "oracle_cards_punctuation.jsonl")
        for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    ]
    with connect(settings) as conn:
        apply_cards(conn, [r for line in lines if (r := normalise(json.loads(line))) is not None])
    return settings


def card_ids(settings: Settings) -> dict[str, UUID]:
    with connect(settings) as conn:
        return dict(conn.execute("SELECT name, oracle_id FROM cards").fetchall())


def client(model: TaggerModel, cap: float | None = None) -> ModelClient:
    return ModelClient(model, "claude-opus-5-5", recorder=MemoryRecorder(), cost_cap_usd=cap)


def test_a_pool_is_tagged_and_stored(loaded: Settings) -> None:
    ids = card_ids(loaded)
    model = TaggerModel()

    with connect(loaded) as conn:
        result = roles_for(conn, client(model), list(ids.values()), batch_size=3)
        stored = load_roles(conn, ids.values())

    assert result.tagged == len(ids) and result.cached == 0 and result.failed == []
    assert sorted(model.tagged) == sorted(ids)
    assert result.roles[ids["Sol Ring"]].roles == ["ramp"]
    assert result.roles[ids["Lim-Dûl's Cohort"]].roles == []
    assert stored[ids["Sol Ring"]].prompt_version == PROMPT_VERSION
    assert stored[ids["Sol Ring"]].model == "claude-opus-5-5"


def test_a_second_request_for_the_same_pool_makes_no_model_calls(loaded: Settings) -> None:
    ids = list(card_ids(loaded).values())

    with connect(loaded) as conn:
        roles_for(conn, client(TaggerModel()), ids, batch_size=3)
        again = TaggerModel()
        result = roles_for(conn, client(again), ids, batch_size=3)

    assert again.tagged == []
    assert (result.tagged, result.cached) == (0, len(ids))
    assert result.cost_usd == 0


def test_only_a_card_whose_text_changed_is_tagged_again(loaded: Settings) -> None:
    ids = card_ids(loaded)
    with connect(loaded) as conn:
        roles_for(conn, client(TaggerModel()), list(ids.values()), batch_size=3)
        conn.execute(
            "UPDATE cards SET content_hash = 'errata' WHERE oracle_id = %s", (ids["Sol Ring"],)
        )
        conn.commit()
        again = TaggerModel()
        roles_for(conn, client(again), list(ids.values()), batch_size=3)

    assert again.tagged == ["Sol Ring"]


def test_tags_from_an_older_prompt_version_are_redone(loaded: Settings) -> None:
    ids = card_ids(loaded)
    with connect(loaded) as conn:
        roles_for(conn, client(TaggerModel()), list(ids.values()), batch_size=3)
        old = load_roles(conn, [ids["Mox Jet"]])[ids["Mox Jet"]]
        save_roles(conn, [old.model_copy(update={"prompt_version": "roles-v1"})])
        again = TaggerModel()
        roles_for(conn, client(again), list(ids.values()), batch_size=3)

    assert again.tagged == ["Mox Jet"]


def test_a_failed_batch_keeps_what_the_others_tagged(loaded: Settings) -> None:
    ids = list(card_ids(loaded).values())
    model = TaggerModel(fail_on="Sol Ring")

    with connect(loaded) as conn:
        result = roles_for(conn, client(model), ids, batch_size=1)
        stored = load_roles(conn, ids)

    sol_ring = card_ids(loaded)["Sol Ring"]
    assert result.failed == [sol_ring]
    assert result.tagged == len(ids) - 1
    assert sol_ring not in stored and len(stored) == len(ids) - 1


def test_a_pool_that_could_exceed_the_cost_cap_is_refused_before_any_call(
    loaded: Settings,
) -> None:
    model = TaggerModel()

    with connect(loaded) as conn, pytest.raises(BudgetExceededError, match="cost cap"):
        roles_for(conn, client(model, cap=0.0001), list(card_ids(loaded).values()))

    assert model.tagged == []


def test_unknown_or_removed_cards_are_skipped(loaded: Settings) -> None:
    ids = card_ids(loaded)

    with connect(loaded) as conn:
        result = roles_for(conn, client(TaggerModel()), [ids["Sol Ring"], UUID(int=1)])

    assert set(result.roles) == {ids["Sol Ring"]}
    assert result.unknown == [UUID(int=1)]


def test_every_batch_carries_the_callers_trace_id(loaded: Settings) -> None:
    ids = list(card_ids(loaded).values())
    recorder = MemoryRecorder()
    model_client = ModelClient(TaggerModel(), "claude-opus-5-5", recorder=recorder)

    with traced() as trace_id, connect(loaded) as conn:
        roles_for(conn, model_client, ids, batch_size=2, parallel=4)

    assert len(recorder.records) > 1
    assert {record.trace_id for record in recorder.records} == {trace_id}


def test_stored_roles_record_the_cards_content_hash(loaded: Settings) -> None:
    ids = card_ids(loaded)
    with connect(loaded) as conn:
        roles_for(conn, client(TaggerModel()), [ids["Sol Ring"]])
        stored: CardRoles = load_roles(conn, [ids["Sol Ring"]])[ids["Sol Ring"]]
        current = conn.execute(
            "SELECT content_hash FROM cards WHERE oracle_id = %s", (ids["Sol Ring"],)
        ).fetchone()

    assert current is not None and stored.content_hash == current[0]
