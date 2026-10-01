"""A client for Scryfall's API and bulk data files. See ADR 0005.

Scryfall asks API users to send a descriptive User-Agent and an Accept header,
and to space requests 50 to 100 ms apart (about 10 a second). Its bulk data
files are served from a separate host with no such limit, and are what the
project ingests: one request for the file's metadata, then one download.
"""

import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx2
from pydantic import BaseModel, ConfigDict

from mtg_deck_advisor.ingestion.downloads import Sleep, download_to_cache, request_with_retries

API_BASE = "https://api.scryfall.com"
# The upper end of Scryfall's requested spacing between API requests.
MIN_REQUEST_INTERVAL_SECONDS = 0.1
ACCEPT = "application/json;q=0.9,*/*;q=0.8"
TIMEOUT_SECONDS = 30.0


class BulkFile(BaseModel):
    """One of Scryfall's daily bulk data files."""

    model_config = ConfigDict(frozen=True)

    type: str
    updated_at: datetime
    # Timestamped, so it never changes once published: safe to cache by URL.
    download_uri: str
    compressed_size: int


class ScryfallClient:
    """Use as a context manager, so the connection pool is closed."""

    def __init__(
        self,
        *,
        user_agent: str,
        cache_dir: Path,
        transport: httpx2.BaseTransport | None = None,
        sleep: Sleep = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = httpx2.Client(
            headers={"User-Agent": user_agent, "Accept": ACCEPT},
            timeout=TIMEOUT_SECONDS,
            transport=transport,
            follow_redirects=True,
        )
        self._cache_dir = cache_dir
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._http.close()

    def _wait_for_turn(self) -> None:
        """Keep API requests at least MIN_REQUEST_INTERVAL_SECONDS apart."""
        if self._last_request_at is not None:
            wait = MIN_REQUEST_INTERVAL_SECONDS - (self._clock() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._clock()

    def bulk_file(self, bulk_type: str) -> BulkFile:
        """The current version of a bulk file, such as "oracle_cards"."""
        url = f"{API_BASE}/bulk-data/{bulk_type.replace('_', '-')}"
        response = request_with_retries(
            self._http, "GET", url, sleep=self._sleep, before_request=self._wait_for_turn
        )
        data: dict[str, Any] = response.json()
        return BulkFile(
            type=data["type"],
            updated_at=data["updated_at"],
            download_uri=data["jsonl_download_uri"],
            compressed_size=data["compressed_size"],
        )

    def download_bulk_file(self, bulk: BulkFile) -> Path:
        """The bulk file on disk, downloaded only if it is not already cached."""
        return download_to_cache(
            self._http,
            bulk.download_uri,
            self._cache_dir,
            expected_size=bulk.compressed_size,
            sleep=self._sleep,
        )
