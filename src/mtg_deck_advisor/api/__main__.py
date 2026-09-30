"""Run the API server: `python -m mtg_deck_advisor.api`.

Logging is configured before uvicorn starts, and uvicorn is told not to set
up logging of its own (log_config=None). Its loggers then propagate to the
root handler, so the server's own lines ("Started server process" and so on)
come out in the same JSON format as the app's. uvicorn's access log is off
because TraceMiddleware logs every request with its trace ID.
"""

import uvicorn

from mtg_deck_advisor.config import Settings, get_settings
from mtg_deck_advisor.observability.logging import configure_logging


def serve(settings: Settings) -> None:
    configure_logging(settings)
    uvicorn.run(
        "mtg_deck_advisor.api.app:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    serve(get_settings())
