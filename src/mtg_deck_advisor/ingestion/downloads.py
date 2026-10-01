"""HTTP requests with retries, and downloads into a local cache (ING-2). See ADR 0005.

Shared by every external source: Scryfall's API and bulk files, and the
Comprehensive Rules file. Source-specific behaviour, such as Scryfall's
required headers and request spacing, lives with the source.
"""

import hashlib
import os
import re
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx2
import structlog
from tenacity import RetryCallState, Retrying, retry_if_exception, stop_after_attempt

log = structlog.get_logger(__name__)

MAX_ATTEMPTS = 5
# The longest single wait, whether from backoff or from a server's Retry-After.
MAX_WAIT_SECONDS = 60.0
# Statuses that can succeed on a later attempt: rate limiting, and server or
# gateway failures. Anything else in the 4xx range is the request's fault and
# would fail the same way again.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

Sleep = Callable[[float], None]


class DownloadError(Exception):
    """A request failed for good: not retryable, or out of attempts."""


class _RetryableStatus(Exception):
    def __init__(self, response: httpx2.Response) -> None:
        super().__init__(f"HTTP {response.status_code} from {response.request.url}")
        self.retry_after = _retry_after_seconds(response)


class _Incomplete(Exception):
    """The connection delivered fewer bytes than promised."""


def _retry_after_seconds(response: httpx2.Response) -> float | None:
    # Retry-After may also be an HTTP date; that form falls back to backoff.
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


def _raise_for_status(response: httpx2.Response) -> None:
    if response.status_code in RETRYABLE_STATUSES:
        raise _RetryableStatus(response)
    if response.status_code >= 400:
        raise DownloadError(f"HTTP {response.status_code} from {response.request.url}")


def _is_retryable(exc: BaseException) -> bool:
    # TransportError covers connection failures and every kind of timeout.
    return isinstance(exc, _RetryableStatus | _Incomplete | httpx2.TransportError)


def _wait_seconds(state: RetryCallState) -> float:
    """Retry-After when the server gives one; otherwise 1, 2, 4, 8... seconds."""
    exc = state.outcome.exception() if state.outcome else None
    if isinstance(exc, _RetryableStatus) and exc.retry_after is not None:
        return min(exc.retry_after, MAX_WAIT_SECONDS)
    return min(2.0 ** (state.attempt_number - 1), MAX_WAIT_SECONDS)


def _log_retry(state: RetryCallState) -> None:
    exc = state.outcome.exception() if state.outcome else None
    log.warning(
        "http_retry",
        attempt=state.attempt_number,
        wait_seconds=state.upcoming_sleep,
        reason=str(exc),
    )


def _retrying(max_attempts: int, sleep: Sleep) -> Retrying:
    return Retrying(
        sleep=sleep,
        stop=stop_after_attempt(max_attempts),
        wait=_wait_seconds,
        retry=retry_if_exception(_is_retryable),
        before_sleep=_log_retry,
        reraise=True,
    )


def request_with_retries(
    client: httpx2.Client,
    method: str,
    url: str,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Sleep = time.sleep,
    before_request: Callable[[], None] | None = None,
) -> httpx2.Response:
    """Make a small request (such as an API call), retrying failures that may pass.

    `before_request` runs before every attempt, retries included, which is
    where a source applies its own rate limit.
    """
    try:
        for attempt in _retrying(max_attempts, sleep):
            with attempt:
                if before_request is not None:
                    before_request()
                response = client.request(method, url)
                _raise_for_status(response)
    except (_RetryableStatus, httpx2.TransportError) as exc:
        raise DownloadError(f"{method} {url} failed after {max_attempts} attempts: {exc}") from exc
    return response


def cache_path(cache_dir: Path, url: str) -> Path:
    """Where a URL's download is cached: a hash of the URL plus a readable name.

    The hash keeps different URLs apart; the name is sanitised so that no URL,
    however odd, can produce a path outside the cache directory.
    """
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    name = PurePosixPath(urlsplit(url).path).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    name = re.sub(r"\.{2,}", ".", name).strip(".")[:100] or "download"
    return cache_dir / f"{digest}-{name}"


def download_to_cache(
    client: httpx2.Client,
    url: str,
    cache_dir: Path,
    *,
    expected_size: int | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Sleep = time.sleep,
) -> Path:
    """Download a URL into the cache, or return the cached copy without a request.

    Only immutable URLs belong here (Scryfall's bulk files and the rules file
    both have their date in the URL), so a cached copy of the right size is
    the right file. The size is checked against `expected_size`, or the
    server's Content-Length; a short download is retried and never cached.
    The file is written beside its final name and renamed into place, so an
    interrupted download cannot leave a partial file that looks complete.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_path(cache_dir, url)
    if target.exists() and (expected_size is None or target.stat().st_size == expected_size):
        log.info("download_cache_hit", url=url, path=str(target))
        return target

    partial = target.with_name(target.name + ".part")
    try:
        for attempt in _retrying(max_attempts, sleep):
            with attempt:
                written, promised = _stream_to_file(client, url, partial, expected_size)
                if promised is not None and written != promised:
                    raise _Incomplete(f"received {written} of {promised} bytes from {url}")
    except (_RetryableStatus, _Incomplete, httpx2.TransportError) as exc:
        partial.unlink(missing_ok=True)
        message = f"download of {url} failed after {max_attempts} attempts: {exc}"
        raise DownloadError(message) from exc
    except DownloadError:
        partial.unlink(missing_ok=True)
        raise

    os.replace(partial, target)
    log.info("download_finished", url=url, path=str(target), bytes=written)
    return target


def _stream_to_file(
    client: httpx2.Client, url: str, path: Path, expected_size: int | None
) -> tuple[int, int | None]:
    """Write the response body to `path`: bytes written, and bytes promised."""
    with client.stream("GET", url) as response:
        _raise_for_status(response)
        promised = expected_size
        if promised is None and "Content-Length" in response.headers:
            promised = int(response.headers["Content-Length"])
        written = 0
        with path.open("wb") as out:
            # Raw bytes, exactly as sent: sizes are compared with what the
            # server promised, before any decoding.
            for chunk in response.iter_raw():
                out.write(chunk)
                written += len(chunk)
    return written, promised
