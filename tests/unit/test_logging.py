import json
import logging
import sys
from collections.abc import Iterator
from typing import Any

import pytest
import structlog

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.observability.logging import add_trace_id, configure_logging
from mtg_deck_advisor.observability.tracing import traced


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """configure_logging changes process-wide state; put it back after each test."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"database_url": "postgresql://x:y@localhost/db"} | overrides
    return Settings(_env_file=None, **values)


def json_lines(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def test_add_trace_id_adds_the_current_trace_id() -> None:
    with traced() as trace_id:
        event = add_trace_id(None, "info", {"event": "hello"})

    assert event["trace_id"] == trace_id


def test_add_trace_id_leaves_events_outside_a_trace_unchanged() -> None:
    event = add_trace_id(None, "info", {"event": "hello"})

    assert "trace_id" not in event


def test_json_format_writes_one_json_object_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(log_format="json"))

    with traced() as trace_id:
        structlog.get_logger("test").info("card_loaded", card="Sol Ring")

    [line] = json_lines(capsys.readouterr().out)
    assert line["event"] == "card_loaded"
    assert line["card"] == "Sol Ring"
    assert line["level"] == "info"
    assert line["trace_id"] == trace_id
    assert "timestamp" in line


def test_standard_library_loggers_get_the_same_json_format(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Third-party libraries (uvicorn, alembic) log through the standard
    # library; their lines must be JSON with the trace ID too.
    configure_logging(settings(log_format="json"))

    with traced() as trace_id:
        logging.getLogger("some.library").warning("disk %s", "full")

    [line] = json_lines(capsys.readouterr().out)
    assert line["event"] == "disk full"
    assert line["level"] == "warning"
    assert line["logger"] == "some.library"
    assert line["trace_id"] == trace_id


def test_messages_below_the_configured_level_are_dropped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(settings(log_format="json", log_level="WARNING"))

    log = structlog.get_logger("test")
    log.info("not_shown")
    log.warning("shown")

    assert [line["event"] for line in json_lines(capsys.readouterr().out)] == ["shown"]


def test_console_format_is_human_readable_not_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(log_format="console"))

    structlog.get_logger("test").info("card_loaded", card="Sol Ring")

    output = capsys.readouterr().out
    assert "card_loaded" in output
    with pytest.raises(json.JSONDecodeError):
        json.loads(output.splitlines()[0])


def test_logs_can_go_to_stderr_leaving_stdout_to_a_protocol(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The MCP server speaks JSON-RPC on stdout; a log line there would corrupt it.
    configure_logging(settings(log_format="json"), stream=sys.stderr)

    structlog.get_logger("test").info("card_loaded")
    logging.getLogger("library").warning("from the standard library")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert [line["event"] for line in json_lines(captured.err)] == [
        "card_loaded",
        "from the standard library",
    ]
