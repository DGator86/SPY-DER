"""Train a model group from recorded sessions (`spy-der-train`).

Offline and deterministic like the engine: it reads the market recordings, has
no network, and produces a registered model group that `spy-der engine
--forecast-group <id>` can then serve.

Run it after enough sessions have been recorded. It is deliberately *not* a
service — training on a timer would silently replace the model behind a running
engine, and swapping the thing that makes predictions is a decision someone
should take on purpose.

Exit codes are distinct because the operator fix differs:

===  ==========================================================================
0    a group was registered
2    no recordings under the state root
3    recordings exist but produced no usable observations
4    observations exist but no component role met the minimum row count
===  ==========================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from spy_der.runtime.heartbeat import write_heartbeat
from spy_der.training.observations import build_observations
from spy_der.training.pipeline import MIN_ROWS_PER_ROLE, train_model_group
from spy_der.training.registry import ALLOWED_MODES, ModelRegistry

__all__ = ["build_arg_parser", "main"]

log = logging.getLogger("spy_der.train")

DEFAULT_STATE_ROOT = "/var/lib/spy-der"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Train a SPY-DER model group from recorded sessions. Offline and "
            "deterministic: reads market recordings, writes a registered group."
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
        "--min-rows",
        type=int,
        default=MIN_ROWS_PER_ROLE,
        help=f"minimum labeled rows before a role is fitted (default: {MIN_ROWS_PER_ROLE})",
    )
    p.add_argument(
        "--status",
        default="research",
        help=(
            "registry status for the new group (default: research). Promotion "
            "beyond this is a separate, human-acknowledged decision."
        ),
    )
    p.add_argument("--group-id", default=None, help="explicit group id (default: derived)")
    p.add_argument(
        "--json",
        action="store_true",
        help="emit the result as JSON on stdout, for scripting",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = build_arg_parser().parse_args(argv)
    state_root = Path(args.state_root)

    if not (state_root / "market").is_dir():
        log.error("no recordings under %s — run spy-der-market first", state_root / "market")
        return 2

    observations = build_observations(state_root, sessions=args.sessions)
    log.info("observations: %s", observations.describe())
    if not len(observations):
        log.error("recordings produced no usable observations")
        return 3

    registry = ModelRegistry(str(state_root / "models"))
    result = train_model_group(
        observations,
        registry=registry,
        status=args.status,
        group_id=args.group_id,
        min_rows=args.min_rows,
    )
    log.info("%s", result.describe())

    payload = {
        "group_id": result.group_id,
        "status": result.status,
        "sessions": list(result.sessions),
        "n_observations": result.n_observations,
        "fold_count": result.fold_count,
        "is_servable": result.is_servable,
        "has_edge": result.has_edge,
        "edge_roles": list(result.edge_roles),
        "roles": [
            {
                "role": r.role,
                "trained": r.trained,
                "model_id": r.model_id,
                "n_rows": r.n_rows,
                "reason": r.reason,
                "skill": r.skill,
                "has_edge": r.has_edge,
                "metrics": r.metrics,
            }
            for r in result.roles
        ],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))

    write_heartbeat(
        str(state_root),
        "train",
        interval_seconds=0.0,
        detail=result.describe(),
        extra=payload,
    )

    if not result.group_id:
        log.error(
            "no component role met the %d-row minimum; record more sessions",
            args.min_rows,
        )
        return 4

    if result.fold_count == 0:
        # Registered, but with no out-of-fold metrics behind it. Worth saying
        # plainly: a group with no held-out score is not evidence of skill.
        log.warning(
            "group %s has no walk-forward folds — metrics are absent, not zero; "
            "record more sessions before trusting it",
            result.group_id,
        )
    elif not result.has_edge:
        # The distinction that matters, and the one a raw loss value hides: this
        # group trains, registers and serves, and forecasts nothing of value.
        log.warning(
            "group %s shows NO EDGE — no component beat a baseline that uses no "
            "features at all. It will serve, but its forecasts carry no "
            "information. Do not promote it past 'research'.",
            result.group_id,
        )
    # The registry gates which load modes a status allows, so the hint has to
    # name the mode that actually matches — a `research` group refuses to load
    # in the engine's default `shadow` mode, and a hint that silently fails is
    # worse than none.
    log.info(
        "serve it with: spy-der-engine --forecast-group %s --forecast-load-mode %s",
        result.group_id,
        _load_mode_for(result.status),
    )
    if result.status == "research":
        log.info(
            "status is 'research' — re-run with --status shadow (or promote the "
            "group) before the engine will serve it in shadow mode"
        )
    return 0


def _load_mode_for(status: str) -> str:
    """The most capable load mode ``status`` permits, per the registry's gates."""
    allowed = ALLOWED_MODES.get(status, frozenset({"research"}))
    for mode in ("champion", "candidate", "shadow", "research"):
        if mode in allowed:
            return mode
    return "research"


if __name__ == "__main__":
    raise SystemExit(main())
