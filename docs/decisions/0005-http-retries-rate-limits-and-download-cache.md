# 0005 - HTTP retries, rate limiting and the download cache

**Status:** Accepted, 2026-10-01
**Applies to:** `mtg_deck_advisor.ingestion.downloads`, `mtg_deck_advisor.ingestion.scryfall`

## Context

Ingestion depends on two external sources: Scryfall (an API plus daily bulk data files) and the Comprehensive Rules text file from Wizards. ING-2 requires caching, retries and rate-limit handling. Specific constraints:

- **Scryfall's API rules:** requests must carry a descriptive `User-Agent` and an `Accept` header, and be spaced 50 to 100 ms apart (about 10 a second). Clients that ignore this can be blocked. The bulk files are on a separate host (`data.scryfall.io`) without that limit.
- **Size:** the card file is about 25 MB compressed and is regenerated daily. Downloading it on every run would be slow, wasteful, and impolite to a free service.
- **Immutable URLs:** both sources publish files under dated URLs (`oracle-cards-20261001090155.jsonl.gz`, `MagicCompRules 20260925.txt`). A URL's content never changes; a new version gets a new URL.
- **Failure modes:** networks drop connections mid-download, and servers return 429 or 5xx under load. A truncated file must never be mistaken for a complete one.

## Decision

- **`httpx2`** for HTTP, the same library the test client uses, with **`tenacity`** for retries.
- **What retries:** 429, 500, 502, 503 and 504, timeouts and connection errors, and downloads shorter than promised. Other 4xx statuses fail at once, since they would fail the same way again.
- **How long between attempts:** the server's `Retry-After` when given. Otherwise exponential backoff: 1, 2, 4, 8 seconds. Every wait is capped at 60 seconds, and a request makes at most 5 attempts.
- **Scryfall rate limit:** a minimum of 100 ms between *API* requests, enforced before every attempt, retries included. Bulk file downloads aren't throttled.
- **Download cache keyed by URL.**
  - A file already in the cache, at the expected size, is returned with no request at all. Because the URLs are immutable, this is always correct, and there's no need for `ETag` revalidation.
  - The cache file name is a hash of the URL plus a sanitised readable name, so no URL can write outside the cache directory.
- **Size check, then atomic rename.** The body is streamed as raw bytes to a `.part` file. Its size is checked against the size the source published (Scryfall's `compressed_size`, otherwise `Content-Length`), and only a complete file is renamed into place. Raw bytes matter: decoding a compressed transfer would make the size comparison meaningless.

## Alternatives

**Conditional requests (`ETag` / `If-None-Match`)** instead of URL-keyed caching. That's the general solution for mutable URLs, but each check is still a request, and these URLs are immutable. Revalidation would buy nothing.

**`requests` plus `urllib3.Retry`.** A familiar combination, but `urllib3.Retry` retries at the connection level and can't retry a download that was cut short mid-stream, or check its size. A separate HTTP library would also mean a second one in the project, besides `httpx2`.

**No cache, download every run.** Simpler, but every ingestion run would cost a 25 MB download, and the ING-3 acceptance check ("the second run re-processes nothing") would still spend most of its time downloading.

**Jitter on backoff.** Jitter spreads out many clients retrying at once. There's one client here, so it would only make retry timing harder to test and reason about.

## Consequences

- A second ingestion run makes one small metadata request and no download. Against the real service: 1.7 s for the first run (24,595,867 bytes), 0.2 s for the second.
- An interrupted or truncated download is retried, and never cached.
- Retries are logged as `http_retry` warnings with the attempt number, wait and reason, so slow or failing runs are visible in the logs.
- The cache grows by one file per daily version fetched. Nothing prunes it yet, which is acceptable at about 25 MB per file for occasional runs. A cleanup step can come later if needed.
- The retry and wait logic is tested with a fake transport and recorded sleeps, so the tests are instant and need no network.
