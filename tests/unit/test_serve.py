import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
import uvicorn

from mtg_deck_advisor.api import __main__ as serve_module
from mtg_deck_advisor.config import Settings


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()


def test_serve_leaves_logging_to_the_app_and_listens_where_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    settings = Settings(
        _env_file=None,
        database_url="postgresql://x:y@127.0.0.1/db",
        api_host="0.0.0.0",  # noqa: S104 - the value under test, not a bind
        api_port=9000,
    )

    serve_module.serve(settings)

    [kwargs] = calls
    # log_config=None stops uvicorn installing its own handlers, so its lines
    # go through the app's JSON formatter instead of bypassing it.
    assert kwargs["log_config"] is None
    assert kwargs["access_log"] is False
    assert kwargs["host"] == "0.0.0.0"  # noqa: S104
    assert kwargs["port"] == 9000


def test_uvicorn_lines_are_json_once_serve_has_configured_logging(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> None:
        logging.getLogger("uvicorn.error").info("Started server process")

    monkeypatch.setattr(uvicorn, "run", fake_run)
    settings = Settings(
        _env_file=None, database_url="postgresql://x:y@127.0.0.1/db", log_format="json"
    )

    serve_module.serve(settings)

    [line] = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert line["event"] == "Started server process"
    assert line["logger"] == "uvicorn.error"


def test_api_listens_on_localhost_port_8000_by_default() -> None:
    settings = Settings(_env_file=None, database_url="postgresql://x:y@127.0.0.1/db")

    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
