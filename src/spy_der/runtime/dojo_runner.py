"""CLI entry for systemd Dojo timers."""

from __future__ import annotations

from spy_der.dojo.runner import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
