"""Shared HTTP transport for live market-data providers.

Every vendor adapter needs the same four things and nothing more: auth, a
request, a parsed response, and a clean failure. This module owns them so the
adapters stay thin and the retry/staleness policy is one implementation rather
than four.

Two rules the adapters depend on:

* **Credentials never appear in an exception, log line, or repr.** Vendor errors
  routinely echo the request URL, and Massive supports `?apiKey=` query auth, so
  a naive ``str(error)`` can leak a key into a journal. :func:`redact` scrubs
  known secret-bearing query parameters, and :class:`ProviderHttpError` carries
  only a redacted message.
* **A provider failure is ``None``, not an exception.** ``CompositeFeed`` treats
  ``None`` as "try the next provider"; an exception escaping an adapter would
  take down the whole tick instead of failing over. Adapters catch
  :class:`ProviderHttpError` and return ``None``.
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DEFAULT_BACKOFF_SECONDS",
    "HttpConfig",
    "ProviderHttpError",
    "get_json",
    "redact",
]

#: Exponential backoff between attempts, per the deploy config's retry block.
DEFAULT_BACKOFF_SECONDS: tuple[float, ...] = (2.0, 4.0, 8.0)

#: Query parameters known to carry credentials across the supported vendors.
_SECRET_QUERY_KEYS = frozenset(
    {"apikey", "api_key", "token", "access_token", "key", "secret", "password"}
)

#: HTTP statuses worth retrying: transient server faults and rate limiting.
#: A 401/403/404 is a configuration error — retrying just burns the tick.
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def redact(url: str) -> str:
    """Return ``url`` with any credential-bearing query value replaced.

    Applied to every message that can reach a log or a journal entry.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "<unparseable-url>"
    if not parts.query:
        return url
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    scrubbed = [
        (k, "REDACTED" if k.lower() in _SECRET_QUERY_KEYS else v) for k, v in pairs
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(scrubbed), parts.fragment)
    )


class ProviderHttpError(RuntimeError):
    """A vendor call failed. The message is always redacted.

    ``status`` is the HTTP status when there was one, else ``None`` (timeout,
    DNS failure, malformed JSON).
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def retryable(self) -> bool:
        """Transport failures and transient statuses are worth another attempt."""
        return self.status is None or self.status in _RETRYABLE_STATUS


@dataclass(frozen=True, slots=True)
class HttpConfig:
    """Per-provider transport policy."""

    timeout_seconds: float = 10.0
    attempts: int = 3
    backoff_seconds: Sequence[float] = field(default=DEFAULT_BACKOFF_SECONDS)
    #: Injected for tests so retries do not actually sleep.
    sleep: Any = time.sleep

    def __post_init__(self) -> None:
        if self.attempts < 1:
            msg = "HttpConfig.attempts must be >= 1"
            raise ValueError(msg)
        if self.timeout_seconds <= 0:
            msg = "HttpConfig.timeout_seconds must be > 0"
            raise ValueError(msg)

    def backoff_for(self, attempt: int) -> float:
        """Delay before retrying after a failed ``attempt`` (0-indexed).

        Runs off the end of the configured schedule by repeating its last value,
        so a longer ``attempts`` never silently becomes a tight loop.
        """
        if not self.backoff_seconds:
            return 0.0
        index = min(attempt, len(self.backoff_seconds) - 1)
        return float(self.backoff_seconds[index])


def _open(url: str, headers: Mapping[str, str], timeout: float) -> dict[str, Any]:
    """One request. Raises :class:`ProviderHttpError` with a redacted message."""
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
    except urllib.error.HTTPError as exc:
        # The body often echoes the request, so it is summarized, not included.
        raise ProviderHttpError(
            f"HTTP {exc.code} from {redact(url)}", status=exc.code
        ) from None
    except urllib.error.URLError as exc:
        raise ProviderHttpError(f"transport failure for {redact(url)}: {exc.reason}") from None
    except TimeoutError:
        raise ProviderHttpError(f"timeout for {redact(url)}") from None

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ProviderHttpError(f"malformed JSON from {redact(url)}") from None
    if not isinstance(parsed, dict):
        raise ProviderHttpError(f"expected a JSON object from {redact(url)}")
    return parsed


def get_json(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    config: HttpConfig | None = None,
) -> dict[str, Any]:
    """GET ``url`` and parse JSON, retrying transient failures.

    Raises :class:`ProviderHttpError` once attempts are exhausted, or
    immediately on a non-retryable status — an adapter converts that to ``None``.
    """
    cfg = config or HttpConfig()
    base_headers = {"Accept": "application/json", "Accept-Encoding": "gzip"}
    if headers:
        base_headers.update(headers)

    last: ProviderHttpError | None = None
    for attempt in range(cfg.attempts):
        try:
            return _open(url, base_headers, cfg.timeout_seconds)
        except ProviderHttpError as exc:
            last = exc
            if not exc.retryable:
                raise
            if attempt + 1 < cfg.attempts:
                delay = cfg.backoff_for(attempt)
                if delay > 0:
                    cfg.sleep(delay)
    assert last is not None  # unreachable: attempts >= 1 guarantees an error
    raise last
