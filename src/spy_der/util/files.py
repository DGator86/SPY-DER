"""Atomic file helpers used by Dojo / learning without importing runtime."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from spy_der.contracts.serialization import to_canonical_json

__all__ = ["atomic_write_json"]

#: `tempfile.mkstemp` hardcodes 0600 and `os.replace` carries that mode onto the
#: target, so every atomically-written file used to land owner-read-only. These
#: are published artifacts — `reports/dojo/latest.json` and `live_state.json` are
#: the documented read surface for the dashboard API and the 0DTE adapter, which
#: run as different users. Publish them world-readable (minus umask) so a reader
#: is not silently EACCES'd; nothing written through here is a secret.
_PUBLISHED_MODE = 0o644


def _published_mode() -> int:
    umask = os.umask(0)
    os.umask(umask)
    return _PUBLISHED_MODE & ~umask


def atomic_write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    """Write JSON atomically (temp + fsync + replace), readable by consumers."""
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
        os.chmod(tmp, _published_mode())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target
