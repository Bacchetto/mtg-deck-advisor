import json
import logging
import re
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from fastapi.testclient import TestClient

from mtg_deck_advisor.api.app import create_app, database_is_reachable
from mtg_deck_advisor.config import Settings

TRACE_ID = re.compile(r"[0-9a-f]{32}")


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()


def client_with_database(reachable: bool) -> TestClient:
    settings = Settings(_env_file=None, database_url="postgresql://x:y@127.0.0.1/db")
    app = create_app(settings)
    app.dependency_overrides[database_is_reachable] = lambda: reachable
    return TestClient(app)


def json_lines(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def test_health_is_ok_when_the_database_is_reachable() -> None:
    with client_with_database(reachable=True) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_is_503_when_the_database_is_unreachable() -> None:
    with client_with_database(reachable=False) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "unreachable"}


def test_every_response_carries_a_new_trace_id() -> None:
    with client_with_database(reachable=True) as client:
        first = client.get("/health").headers["X-Trace-Id"]
        second = client.get("/health").headers["X-Trace-Id"]

    assert TRACE_ID.fullmatch(first)
    assert TRACE_ID.fullmatch(second)
    assert first != second


def test_a_trace_id_sent_by_the_client_is_ignored() -> None:
    # Request headers are untrusted input: a client must not be able to
    # choose, reuse or impersonate another request's trace ID.
    with client_with_database(reachable=True) as client:
        response = client.get("/health", headers={"X-Trace-Id": "a" * 32})

    assert response.headers["X-Trace-Id"] != "a" * 32


def test_each_request_is_logged_with_its_trace_id(capsys: pytest.CaptureFixture[str]) -> None:
    with client_with_database(reachable=True) as client:
        trace_id = client.get("/health").headers["X-Trace-Id"]

    [finished] = [
        line for line in json_lines(capsys.readouterr().out) if line["event"] == "request_finished"
    ]
    assert finished["trace_id"] == trace_id
    assert finished["method"] == "GET"
    assert finished["path"] == "/health"
    assert finished["status_code"] == 200
    assert finished["duration_ms"] >= 0


def test_logs_from_sync_dependencies_carry_the_trace_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # FastAPI runs sync dependencies in a thread pool. The trace ID must
    # survive the hop from the event loop to the worker thread.
    def logging_check() -> bool:
        structlog.get_logger("test").info("checking_database")
        return True

    settings = Settings(_env_file=None, database_url="postgresql://x:y@127.0.0.1/db")
    app = create_app(settings)
    app.dependency_overrides[database_is_reachable] = logging_check

    with TestClient(app) as client:
        trace_id = client.get("/health").headers["X-Trace-Id"]

    [checking] = [
        line for line in json_lines(capsys.readouterr().out) if line["event"] == "checking_database"
    ]
    assert checking["trace_id"] == trace_id


def test_openapi_documentation_is_generated() -> None:
    with client_with_database(reachable=True) as client:
        schema = client.get("/openapi.json").json()

    assert "/health" in schema["paths"]
