"""Retries and the download cache, against a fake transport: no network."""

from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest

from mtg_deck_advisor.ingestion.downloads import (
    DownloadError,
    cache_path,
    download_to_cache,
    request_with_retries,
)

URL = "https://data.example.test/oracle-cards/oracle-cards-20261001090155.jsonl.gz"
BODY = b"x" * 1000

Handler = Callable[[httpx2.Request], httpx2.Response]


class Recorder:
    """A fake server: answers with the given responses in turn, counting requests."""

    def __init__(self, *responses: httpx2.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


def client_for(handler: Handler) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handler))


class Sleeps:
    """Records requested sleeps instead of sleeping, so retry tests run instantly."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# --- request_with_retries --------------------------------------------------


def test_a_successful_request_is_made_once() -> None:
    server = Recorder(httpx2.Response(200, json={"ok": True}))

    response = request_with_retries(client_for(server), "GET", URL, sleep=Sleeps())

    assert response.json() == {"ok": True}
    assert len(server.requests) == 1


def test_a_server_error_is_retried_until_it_succeeds() -> None:
    server = Recorder(httpx2.Response(500), httpx2.Response(503), httpx2.Response(200))
    sleeps = Sleeps()

    response = request_with_retries(client_for(server), "GET", URL, sleep=sleeps)

    assert response.status_code == 200
    assert len(server.requests) == 3
    assert len(sleeps.calls) == 2
    assert sleeps.calls[1] > sleeps.calls[0]  # backing off


def test_a_persistent_server_error_gives_up_after_the_maximum_attempts() -> None:
    server = Recorder(httpx2.Response(500))

    with pytest.raises(DownloadError, match="500"):
        request_with_retries(client_for(server), "GET", URL, max_attempts=4, sleep=Sleeps())

    assert len(server.requests) == 4


def test_rate_limiting_waits_as_long_as_retry_after_says() -> None:
    server = Recorder(httpx2.Response(429, headers={"Retry-After": "7"}), httpx2.Response(200))
    sleeps = Sleeps()

    request_with_retries(client_for(server), "GET", URL, sleep=sleeps)

    assert sleeps.calls == [7.0]


def test_an_unreasonable_retry_after_is_capped() -> None:
    server = Recorder(httpx2.Response(429, headers={"Retry-After": "86400"}), httpx2.Response(200))
    sleeps = Sleeps()

    request_with_retries(client_for(server), "GET", URL, sleep=sleeps)

    assert sleeps.calls == [60.0]


def test_a_timeout_is_retried() -> None:
    server = Recorder(httpx2.ReadTimeout("slow"), httpx2.Response(200))

    response = request_with_retries(client_for(server), "GET", URL, sleep=Sleeps())

    assert response.status_code == 200
    assert len(server.requests) == 2


def test_a_client_error_is_not_retried() -> None:
    # A 404 will not fix itself; retrying would only add load and delay.
    server = Recorder(httpx2.Response(404))

    with pytest.raises(DownloadError, match="404"):
        request_with_retries(client_for(server), "GET", URL, sleep=Sleeps())

    assert len(server.requests) == 1


# --- download_to_cache -----------------------------------------------------


def test_a_download_is_saved_in_the_cache(tmp_path: Path) -> None:
    server = Recorder(httpx2.Response(200, content=BODY))

    path = download_to_cache(client_for(server), URL, tmp_path, expected_size=len(BODY))

    assert path.read_bytes() == BODY
    assert path.parent == tmp_path


def test_a_cached_download_makes_no_request(tmp_path: Path) -> None:
    server = Recorder(httpx2.Response(200, content=BODY))
    client = client_for(server)

    first = download_to_cache(client, URL, tmp_path, expected_size=len(BODY))
    second = download_to_cache(client, URL, tmp_path, expected_size=len(BODY))

    assert first == second
    assert len(server.requests) == 1


def test_a_truncated_download_is_rejected_and_leaves_nothing_behind(tmp_path: Path) -> None:
    server = Recorder(httpx2.Response(200, content=BODY[:400]))

    with pytest.raises(DownloadError, match="400"):
        download_to_cache(
            client_for(server), URL, tmp_path, expected_size=len(BODY), sleep=Sleeps()
        )

    assert list(tmp_path.iterdir()) == []


def test_a_cached_file_of_the_wrong_size_is_downloaded_again(tmp_path: Path) -> None:
    cache_path(tmp_path, URL).write_bytes(b"partial")
    server = Recorder(httpx2.Response(200, content=BODY))

    path = download_to_cache(client_for(server), URL, tmp_path, expected_size=len(BODY))

    assert path.read_bytes() == BODY
    assert len(server.requests) == 1


def test_without_an_expected_size_the_content_length_is_checked(tmp_path: Path) -> None:
    # The server promises 1000 bytes but the connection delivers 400.
    server = Recorder(
        httpx2.Response(200, content=BODY[:400], headers={"Content-Length": str(len(BODY))})
    )

    with pytest.raises(DownloadError):
        download_to_cache(client_for(server), URL, tmp_path, sleep=Sleeps())


def test_a_failed_download_is_retried(tmp_path: Path) -> None:
    server = Recorder(httpx2.Response(502), httpx2.Response(200, content=BODY))

    path = download_to_cache(
        client_for(server), URL, tmp_path, expected_size=len(BODY), sleep=Sleeps()
    )

    assert path.read_bytes() == BODY
    assert len(server.requests) == 2


def test_cache_file_names_are_safe_whatever_the_url(tmp_path: Path) -> None:
    hostile = "https://example.test/../../etc/passwd?x=/../../y"

    path = cache_path(tmp_path, hostile)

    assert path.parent == tmp_path
    assert ".." not in path.name


def test_different_urls_get_different_cache_files(tmp_path: Path) -> None:
    assert cache_path(tmp_path, URL) != cache_path(tmp_path, URL.replace("2026", "2027"))
