"""Console entry points for SPY-DER."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        print("Usage: spy-der <command>")
        print()
        print("Commands:")
        print("  vps-runner   Run VPS parallel-track state publisher")
        return 0
    cmd, *rest = argv
    if cmd in {"vps-runner", "runner"}:
        from spy_der.runtime.runner import main as runner_main

        return runner_main(rest)
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
