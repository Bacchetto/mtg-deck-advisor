from pathlib import Path

import pytest

INTEGRATION_DIR = Path(__file__).parent / "integration"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test under tests/integration/ as `integration`.

    Marking by location rather than by hand means an integration test can
    never forget its marker and slip into the fast, Docker-free unit job.
    """
    for item in items:
        if INTEGRATION_DIR in item.path.parents:
            item.add_marker(pytest.mark.integration)
