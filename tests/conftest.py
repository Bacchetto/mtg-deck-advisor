from pathlib import Path

import pytest

INTEGRATION_DIR = Path(__file__).parent / "integration"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        help="also run tests marked `live`, which call real (possibly paid) services",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark tests under tests/integration/ as `integration`; skip `live` tests unless --live.

    Marking by location rather than by hand means an integration test can
    never forget its marker and slip into the fast, Docker-free unit job.
    Live tests call real services, some of them paid, so they run only when
    asked for explicitly; CI never asks.
    """
    run_live = config.getoption("--live", default=False)
    skip_live = pytest.mark.skip(reason="calls a real service; run with --live")
    for item in items:
        if INTEGRATION_DIR in item.path.parents:
            item.add_marker(pytest.mark.integration)
        if "live" in item.keywords and not run_live:
            item.add_marker(skip_live)
