"""Console entry points for SPY-DER."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        print("Usage: spy-der <command>")
        print()
        print("Commands:")
        print("  market             Run market-data ingestion (providers -> recording)")
        print("  vps-runner         Run VPS parallel-track state publisher")
        print("  ai-check           Verify the live AI decision maker end-to-end")
        print("  decision-service   Local HTTP /v1/decision boundary for 0DTE")
        print("  dashboard-api      Read-only HTTP API for Dojo reports + live state")
        print("  dojo               Run protocol-driven Dojo training cycle")
        print("  validate           Run parity gates over recorded sessions (reports only)")
        return 0
    cmd, *rest = argv
    if cmd in {"market", "market-service"}:
        from spy_der.runtime.market_service import main as market_main

        return market_main(rest)
    if cmd in {"vps-runner", "runner"}:
        from spy_der.runtime.runner import main as runner_main

        return runner_main(rest)
    if cmd in {"ai-check", "aicheck"}:
        from spy_der.runtime.ai_check import main as ai_check_main

        return ai_check_main(rest)
    if cmd in {"decision-service", "decision_service", "serve-decision"}:
        from spy_der.runtime.decision_service import main as decision_main

        return decision_main(rest)
    if cmd in {"dashboard-api", "dashboard_api", "dashboard"}:
        from spy_der.runtime.dashboard_api import main as dashboard_main

        return dashboard_main(rest)
    if cmd in {"dojo", "dojo-runner"}:
        from spy_der.runtime.dojo_runner import main as dojo_main

        return dojo_main(rest)
    if cmd in {"validate", "validation"}:
        from spy_der.runtime.validation import main as validate_main

        return validate_main(rest)
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
