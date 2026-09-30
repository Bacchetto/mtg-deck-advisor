from fastapi.testclient import TestClient

from mtg_deck_advisor.api.app import create_app
from mtg_deck_advisor.config import Settings


def test_health_is_ok_against_a_real_database(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_is_503_when_nothing_is_listening() -> None:
    # Port 1 on the loopback address: the connection is refused at once, so
    # this exercises the real failure path without waiting for a timeout.
    settings = Settings(_env_file=None, database_url="postgresql://x:y@127.0.0.1:1/db")

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "unreachable"}
