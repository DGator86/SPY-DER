"""Read the promoted champion config on the live decision path.

``configs/champion.json`` is written by :mod:`spy_der.learning.promotion` — by a
human ack, or by the Dojo itself once a promotion trial has validated the
change. This module is the other half of that loop: without it a promotion
would be a file nobody reads, and "integrated" would mean nothing.

Reads are cached on (path, mtime, size), so the hot decision path pays one
`stat` per tick and re-reads only when a promotion actually lands. Anything
unreadable or malformed degrades to "no knobs" — a bad config must never take
the decision service down, and empty knobs are exactly the pre-promotion
behaviour.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Any

from spy_der.decisions.knobs import DecisionKnobs

__all__ = [
    "champion_provenance",
    "load_champion_knobs",
    "reset_champion_cache",
]

_CACHE_LOCK = Lock()
_CACHE_KEY: tuple[str, int, int] | None = None
_CACHE_PAYLOAD: dict[str, Any] | None = None


def _champion_path(configs_dir: str | Path | None = None) -> Path:
    if configs_dir is None:
        from spy_der.dojo.config import DEFAULT_CONFIGS_DIR

        configs_dir = DEFAULT_CONFIGS_DIR
    from spy_der.learning.promotion import CHAMPION_FILENAME

    return Path(configs_dir) / CHAMPION_FILENAME


def _load_payload(configs_dir: str | Path | None = None) -> dict[str, Any]:
    """Return the champion payload, cached until the file changes."""
    global _CACHE_KEY, _CACHE_PAYLOAD

    path = _champion_path(configs_dir)
    try:
        stat = path.stat()
    except OSError:
        with _CACHE_LOCK:
            _CACHE_KEY = None
            _CACHE_PAYLOAD = None
        return {}

    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _CACHE_LOCK:
        if key == _CACHE_KEY and _CACHE_PAYLOAD is not None:
            return _CACHE_PAYLOAD

    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    with _CACHE_LOCK:
        _CACHE_KEY = key
        _CACHE_PAYLOAD = payload
    return payload


def _knob_source(payload: dict[str, Any]) -> dict[str, Any]:
    """Champion knobs live at ``knobs``; older files carry hypothesis.change."""
    knobs = payload.get("knobs")
    if isinstance(knobs, dict):
        return knobs
    hypothesis = payload.get("hypothesis")
    if isinstance(hypothesis, dict):
        change = hypothesis.get("change")
        if isinstance(change, dict):
            return change
    return {}


def load_champion_knobs(configs_dir: str | Path | None = None) -> DecisionKnobs:
    """Knobs from the promoted champion — empty when nothing is promoted.

    Set ``SPY_DER_CHAMPION_KNOBS=0`` to ignore the promoted config entirely
    (kill switch: reverts the live path to pre-promotion behaviour without
    deleting the audit trail).
    """
    if os.environ.get("SPY_DER_CHAMPION_KNOBS", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return DecisionKnobs()
    return DecisionKnobs.from_mapping(_knob_source(_load_payload(configs_dir)))


def champion_provenance(configs_dir: str | Path | None = None) -> dict[str, Any]:
    """Identity of the running champion, for reports and dashboards."""
    payload = _load_payload(configs_dir)
    if not payload:
        return {}
    return {
        "candidate_id": payload.get("candidate_id"),
        "status": payload.get("status"),
        "promoted_at": payload.get("promoted_at"),
        "promoted_by": payload.get("promoted_by")
        or ("human" if payload.get("promoted_with_ack") else None),
        "knobs": _knob_source(payload),
    }


def reset_champion_cache() -> None:
    """Drop the cached champion payload (tests / after an in-process promote)."""
    global _CACHE_KEY, _CACHE_PAYLOAD
    with _CACHE_LOCK:
        _CACHE_KEY = None
        _CACHE_PAYLOAD = None
