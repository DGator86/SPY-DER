"""Atomic JSON writers for VPS / dashboard live state."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from spy_der.contracts.serialization import to_canonical_json

__all__ = ["atomic_write_json", "write_live_state_file"]


def atomic_write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    """Write JSON atomically (temp + fsync + replace)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = to_canonical_json(payload)
    # Pretty-print for operators inspecting the file on the VPS.
    pretty = json.dumps(json.loads(data), indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(
        dir=str(target.parent), prefix=".live_state_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(pretty)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def write_live_state_file(path: str | Path, payload: dict[str, Any]) -> Path:
    """Alias used by the VPS runner."""
    return atomic_write_json(path, payload)
