"""Import 0DTE tick recordings (`spy-der-import-zerodte`).

0DTE has been recording every live tick as ``ticks_YYYY-MM-DD.jsonl.gz`` since
long before SPY-DER had its own market service. That is real recorded market
history, and this turns it into SPY-DER canonical recordings so
``spy-der-train`` can be fitted on actual markets today rather than after weeks
of fresh recording.

``--source`` is **required and has no default**, deliberately. SPY-DER must
deploy without 0DTE present, and `tests/unit/test_deploy_independence.py` fails
on any legacy path in package source — so the operator names the directory
rather than the package assuming one. See `docs/ops/IMPORT_ZERODTE.md` for the
conventional location and the full migration sequence.

Read-only with respect to 0DTE: it never writes to the source directory.
Already-imported sessions are skipped unless ``--overwrite`` is given, so a
re-run is cheap and safe.

Exit codes:

===  ==========================================================================
0    at least one session was imported (or all were already present)
2    the source directory does not exist or holds no recordings
===  ==========================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from spy_der.integrations.zerodte.tick_import import (
    DEFAULT_BAR_WINDOW,
    import_directory,
)

__all__ = ["build_arg_parser", "main"]

log = logging.getLogger("spy_der.zerodte_import")

DEFAULT_STATE_ROOT = "/var/lib/spy-der"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Convert 0DTE tick recordings into SPY-DER canonical market "
            "recordings. Read-only with respect to the source."
        )
    )
    p.add_argument(
        "--source",
        required=True,
        help=(
            "directory holding 0DTE ticks_*.jsonl.gz recordings. Required: "
            "SPY-DER does not assume a legacy path"
        ),
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
        "--bar-window",
        type=int,
        default=DEFAULT_BAR_WINDOW,
        help=(
            "1-minute bars retained per snapshot "
            f"(default: {DEFAULT_BAR_WINDOW}, one session)"
        ),
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="re-import sessions already present under <state-root>/market",
    )
    p.add_argument("--json", action="store_true", help="emit the result as JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = build_arg_parser().parse_args(argv)
    source = Path(args.source)

    if not source.is_dir():
        log.error("source directory %s does not exist", source)
        return 2
    if not any(source.glob("ticks_*.jsonl.gz")):
        log.error("no ticks_*.jsonl.gz recordings under %s", source)
        return 2

    result = import_directory(
        source,
        args.state_root,
        sessions=args.sessions,
        bar_window=args.bar_window,
        overwrite=args.overwrite,
    )
    log.info("imported: %s", result.describe())

    if result.ticks_without_chain:
        # Older recordings predate `option_rows`. Those snapshots still carry
        # spot and bars, so the technical features survive, but GEX, flow and
        # RND cannot be rebuilt from them — worth saying rather than leaving an
        # operator to wonder why some sessions train thinner than others.
        log.warning(
            "%d tick(s) had no option_rows — those snapshots have no option "
            "chain, so GEX/flow/RND features are absent for them",
            result.ticks_without_chain,
        )

    if args.json:
        print(
            json.dumps(
                {
                    "sessions_written": list(result.sessions_written),
                    "snapshots_written": result.snapshots_written,
                    "ticks_without_chain": result.ticks_without_chain,
                    "settlements": result.settlements,
                    "skipped": [list(s) for s in result.skipped],
                },
                indent=2,
                sort_keys=True,
            )
        )

    refused = [s for s, why in result.skipped if "live records" in why]
    if refused:
        log.warning(
            "%d session(s) were not overwritten because SPY-DER recorded them "
            "live: %s. Move those files aside if you really mean to replace them.",
            len(refused),
            ", ".join(refused),
        )

    if result.sessions_written:
        log.info(
            "now train on it: spy-der-train --state-root %s", args.state_root
        )
    else:
        log.info("nothing new to import (use --overwrite to re-import)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
