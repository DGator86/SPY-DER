"""Concrete xAI / OpenAI-compatible HTTP transport for Grok.

Uses stdlib urllib only. Never logs Authorization headers or API keys.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

__all__ = ["HttpGrokTransport", "make_http_grok_transport"]


class HttpGrokTransport:
    """POST JSON to chat-completions style endpoints."""

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> str:
        payload = json.dumps(body).encode("utf-8")
        # Strip empty Authorization to avoid sending "Bearer " with no key.
        clean_headers = {k: v for k, v in headers.items() if v}
        req = urllib.request.Request(
            url,
            data=payload,
            headers=clean_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read()
            return bytes(raw).decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"http_{exc.code}:{detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"url_error:{exc.reason}") from exc


def make_http_grok_transport(*, timeout_seconds: float = 30.0) -> HttpGrokTransport:
    return HttpGrokTransport(timeout_seconds=timeout_seconds)
