"""Agent packet security invariants (spec §37.1, §41, §45)."""

from __future__ import annotations

from typing import Any

__all__ = [
    "FORBIDDEN_PACKET_KEYS",
    "assert_no_secrets",
    "redact_secrets",
]

FORBIDDEN_PACKET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "credential",
        "xai_api_key",
        "openai_api_key",
        "broker_token",
        "private_key",
    }
)


def _walk(obj: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_l = str(key).lower()
            child = f"{path}.{key}" if path else str(key)
            if any(bad in key_l for bad in FORBIDDEN_PACKET_KEYS):
                hits.append(child)
            hits.extend(_walk(value, child))
    elif isinstance(obj, (list, tuple)):
        for i, value in enumerate(obj):
            hits.extend(_walk(value, f"{path}[{i}]"))
    return hits


def assert_no_secrets(payload: dict[str, Any]) -> None:
    """Raise if payload appears to contain credential-bearing keys."""
    hits = _walk(payload)
    if hits:
        raise ValueError(f"forbidden secret-bearing keys in packet: {hits}")


def redact_secrets(text: str) -> str:
    """Best-effort redaction for logs/prompts (never persist raw secrets)."""
    # Keep simple: mask long hex/base64-looking tokens after known prefixes.
    import re

    patterns = [
        (r"(?i)(api[_-]?key\s*[=:]\s*)(\S+)", r"\1***"),
        (r"(?i)(authorization\s*[=:]\s*bearer\s+)(\S+)", r"\1***"),
        (r"(?i)(xai[_-]?api[_-]?key\s*[=:]\s*)(\S+)", r"\1***"),
    ]
    out = text
    for pattern, repl in patterns:
        out = re.sub(pattern, repl, out)
    return out
