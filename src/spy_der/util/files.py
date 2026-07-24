"""Atomic file helpers used by Dojo / learning without importing runtime."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from spy_der.contracts.serialization import to_canonical_json

__all__ = ["atomic_write_json"]


def atomic_write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    """Write JSON atomically (temp + fsync + replace)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = to_canonical_json(payload)
    pretty = json.dumps(json.loads(data), indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(
        dir=str(target.parent), prefix=".atomic_", suffix=".tmp"
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
