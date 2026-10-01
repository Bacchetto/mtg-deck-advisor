"""The Scryfall client, against a fake transport: no network."""

from datetime import UTC, datetime
from pathlib import Path

import httpx2

from mtg_deck_advisor.ingestion.scryfall import BulkFile, ScryfallClient

ORACLE_CARDS = {
    "object": "bulk_data",
    "id": "27bf3214-1271-490b-bdfe-c0be6c23d02e",
    "type": "oracle_cards",
    "updated_at": "2026-10-01T09:01:55.977+00:00",
    "uri": "https://api.scryfall.com/bulk-data/27bf3214-1271-490b-bdfe-c0be6c23d02e",
    "name": "Oracle Cards",
    "description": "A JSON file containing one Scryfall card object for each Oracle ID.",
    "jsonl_download_uri": "https://data.scryfall.io/oracle-cards/oracle-cards-20261001090155.jsonl.gz",
    "compressed_size": 1000,
}


class FakeClock:
    """A clock that only moves when told to, recording the sleeps asked of it."""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def scryfall(
    handler: httpx2.MockTransport, tmp_path: Path, clock: FakeClock | None = None
) -> ScryfallClient:
    clock = clock or FakeClock()
    return ScryfallClient(
        user_agent="mtg-deck-advisor-tests/0.1",
        cache_dir=tmp_path,
        transport=handler,
        sleep=clock.sleep,
        clock=clock.monotonic,
    )


def test_bulk_file_metadata_is_read_from_the_api(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=ORACLE_CARDS)

    with scryfall(httpx2.MockTransport(handler), tmp_path) as client:
        bulk = client.bulk_file("oracle_cards")

    assert bulk == BulkFile(
        type="oracle_cards",
        updated_at=datetime(2026, 10, 1, 9, 1, 55, 977000, tzinfo=UTC),
        download_uri=ORACLE_CARDS["jsonl_download_uri"],
        compressed_size=1000,
    )
    [request] = requests
    assert str(request.url) == "https://api.scryfall.com/bulk-data/oracle-cards"


def test_every_request_carries_the_headers_scryfall_requires(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=ORACLE_CARDS)

    with scryfall(httpx2.MockTransport(handler), tmp_path) as client:
        client.bulk_file("oracle_cards")

    [request] = requests
    assert request.headers["User-Agent"] == "mtg-deck-advisor-tests/0.1"
    assert request.headers["Accept"].startswith("application/json")


def test_api_requests_are_spaced_at_least_100_ms_apart(tmp_path: Path) -> None:
    clock = FakeClock()

    def handler(request: httpx2.Request) -> httpx2.Response:
        clock.now += 0.03  # each request takes 30 ms
        return httpx2.Response(200, json=ORACLE_CARDS)

    with scryfall(httpx2.MockTransport(handler), tmp_path, clock) as client:
        client.bulk_file("oracle_cards")
        client.bulk_file("oracle_cards")
        client.bulk_file("oracle_cards")

    # No wait before the first request; then the remaining 70 ms of each gap.
    assert [round(s, 3) for s in clock.sleeps] == [0.07, 0.07]


def test_downloading_a_bulk_file_uses_the_cache(tmp_path: Path) -> None:
    body = b"y" * 1000
    downloads: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "data.scryfall.io":
            downloads.append(request)
            return httpx2.Response(200, content=body)
        return httpx2.Response(200, json=ORACLE_CARDS)

    with scryfall(httpx2.MockTransport(handler), tmp_path) as client:
        bulk = client.bulk_file("oracle_cards")
        first = client.download_bulk_file(bulk)
        second = client.download_bulk_file(bulk)

    assert first == second
    assert first.read_bytes() == body
    assert len(downloads) == 1
