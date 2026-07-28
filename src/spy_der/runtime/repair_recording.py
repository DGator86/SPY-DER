"""Repair market recordings with sequence gaps (`spy-der-repair-recording`).

``ReplayFeed`` fails closed on ``SEQUENCE_GAP``, so training and Dojo skip the
whole session. When the only fault is a restarted sequence counter (the
pre-resume ``spy-der-market`` bug: ``…, 576, 0, 1, …``), this renumbers ``seq``
to ``0..n-1`` after verifying every content hash. Hash mismatches still refuse.

Exit codes:

===  ==========================================================================
0    every targeted recording is clean, or every sequence gap was repaired
2    a recording is missing / unreadable / not a recoverable sequence gap
===  ==========================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from spy_der.market_data.repair import RepairError, repair_state_root

__all__ = ["build_arg_parser", "main"]

log = logging.getLogger("spy_der.repair_recording")

DEFAULT_STATE_ROOT = "/var/lib/spy-der"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Renumber seq on market recordings whose only integrity failure is "
            "a SEQUENCE_GAP. Leaves a .bak beside each rewritten file."
        )
    )
    p.add_argument("--state-root", default=DEFAULT_STATE_ROOT)
    p.add_argument(
        "--session",
        action="append",
        dest="sessions",
        default=None,
        help="restrict to one YYYY-MM-DD session; repeatable",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="classify and report only; do not write",
    )
    p.add_argument(
        "--no-backup",
        action="store_true",
        help="skip writing the .bak sibling (not recommended)",
    )
    p.add_argument("--json", action="store_true", help="emit reports as JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = build_arg_parser().parse_args(argv)
    state_root = Path(args.state_root)

    try:
        reports = repair_state_root(
            state_root,
            sessions=args.sessions,
            dry_run=args.dry_run,
            backup=not args.no_backup,
        )
    except RepairError as exc:
        log.error("%s", exc)
        return 2

    if args.sessions:
        found = {Path(r.path).stem for r in reports}
        missing = [s for s in args.sessions if s not in found]
        if missing:
            log.error("session(s) not found under %s/market: %s", state_root, ", ".join(missing))
            return 2

    if not reports:
        log.error("no recordings under %s/market", state_root)
        return 2

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "path": r.path,
                        "status": r.status,
                        "records": r.records,
                        "sequence_breaks": r.sequence_breaks,
                        "skipped_unparseable": r.skipped_unparseable,
                        "rewritten": r.rewritten,
                        "backup_path": r.backup_path,
                        "detail": r.detail,
                    }
                    for r in reports
                ],
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for report in reports:
            if report.status in {"clean", "repaired"} or (
                args.dry_run and report.status == "sequence_gap"
            ):
                log.info("%s", report.describe())
            elif report.status == "empty":
                log.warning("%s", report.describe())
            else:
                log.error("%s", report.describe())

    failed = [
        r
        for r in reports
        if r.status not in {"clean", "repaired", "empty"}
        and not (args.dry_run and r.status == "sequence_gap")
    ]
    if failed:
        return 2
    repaired = sum(1 for r in reports if r.rewritten)
    gaps = sum(1 for r in reports if r.status == "sequence_gap")
    if args.dry_run and gaps:
        log.info(
            "dry-run: %d recording(s) would be renumbered under %s",
            gaps,
            state_root / "market",
        )
    elif repaired:
        log.info(
            "repaired %d recording(s); re-run: spy-der-train --state-root %s",
            repaired,
            state_root,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
