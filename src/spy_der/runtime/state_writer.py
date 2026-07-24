"""Atomic JSON writers for VPS / dashboard live state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from spy_der.util.files import atomic_write_json

__all__ = ["atomic_write_json", "write_live_state_file"]


def write_live_state_file(path: str | Path, payload: dict[str, Any]) -> Path:
    """Alias used by the VPS runner."""
    return atomic_write_json(path, payload)
