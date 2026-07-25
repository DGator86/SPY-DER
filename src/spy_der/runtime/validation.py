"""Parity validation over recorded sessions (`spy-der validate`).

Drives `spy-der-validation-daily.service` and `-weekly.service`, which have
shipped an ExecStart for `spy-der validate` since the cutover plan was written
without the command existing.

The gates and tolerances are `docs/CUTOVER_PLAN.md`. This is reports-only: it
never promotes, never trades, and only ever reads the state root (plus the
report it writes).

A gate reports one of three verdicts, and the distinction is the point:

``pass`` / ``fail``
    The gate ran against real recorded data and reached a verdict.
``pending``
    The gate cannot run yet — it needs a capability that has not landed, or a
    parallel 0DTE run to compare against. A pending gate is never counted as a
    pass, because a validation report that silently green-lights ungated
    quantities is worse than no report at all.

Exit status is non-zero when any gate fails, so a failing timer shows up as a
failed unit rather than a green one with bad news buried in JSON.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from spy_der.contracts.market import FeedStatus
from spy_der.market_data.replay import CorruptRecordingError, ReplayFeed
from spy_der.util.files import atomic_write_json

__all__ = [
    "DEFAULT_REPORTS_DIR",
    "GateResult",
    "ValidationConfig",
    "main",
    "run_validation",
]

log = logging.getLogger("spy_der.validate")

ET = ZoneInfo("America/New_York")

DEFAULT_STATE_ROOT = "/var/lib/spy-der"
DEFAULT_REPORTS_DIR = str(Path(DEFAULT_STATE_ROOT) / "reports" / "validation")

#: Feed states that mean a component was not usable at snapshot time.
_DEGRADED = frozenset({FeedStatus.STALE, FeedStatus.MISSING, FeedStatus.INVALID})

#: Gates from `docs/CUTOVER_PLAN.md` that cannot run from one side's recordings.
#: Each needs a parallel 0DTE run over identical inputs
#: (`spy_der.runtime.parity.assert_identical_inputs`), so validating them here
#: would mean comparing SPY-DER against itself and always passing.
_PARITY_GATES_NEEDING_BOTH_RUNTIMES = {
    "candidate_ids": "exact; needs a parallel 0DTE run over identical inputs",
    "candidate_geometry_hash": "exact; needs a parallel 0DTE run",
    "maximum_loss": "exact; needs a parallel 0DTE run",
    "capital_required": "±1 cent; needs a parallel 0DTE run",
    "hard_vetoes": "exact set equality; needs a parallel 0DTE run",
    "position_sizing": "exact; needs a parallel 0DTE run",
    "size_scalar": "±1e-9; needs a parallel 0DTE run",
    "settlement": "exact; needs a parallel 0DTE run and a settled session",
    "forecast_probabilities": "±1e-6; needs a parallel 0DTE run",
    "forecast_cone_bounds": "±1e-6 relative; needs a parallel 0DTE run",
    "feature_values": "±1e-9 relative; needs a parallel 0DTE run",
    "regime_labels": "exact; needs a parallel 0DTE run",
    "journal_output": "exact on payload content hash; needs a parallel 0DTE run",
}


@dataclass(frozen=True, slots=True)
class GateResult:
    """One parity gate's verdict."""

    gate: str
    verdict: str  # pass | fail | pending
    detail: str
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "verdict": self.verdict,
            "detail": self.detail,
            "metrics": self.metrics,
        }


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    state_root: str = DEFAULT_STATE_ROOT
    reports_dir: str = DEFAULT_REPORTS_DIR
    window: str = "recent"
    days: int = 5
    report_date: str | None = None

    @property
    def market_dir(self) -> Path:
        return Path(self.state_root) / "market"


def _sessions_in_window(cfg: ValidationConfig, today: dt.date) -> list[Path]:
    """Recording files inside the window, oldest first.

    Files are named `YYYY-MM-DD.jsonl` by the market service. A file whose name
    is not a date is ignored rather than guessed at.
    """
    if not cfg.market_dir.is_dir():
        return []
    cutoff = today - dt.timedelta(days=max(0, cfg.days))
    selected: list[tuple[dt.date, Path]] = []
    for path in cfg.market_dir.glob("*.jsonl"):
        try:
            session = dt.date.fromisoformat(path.stem)
        except ValueError:
            log.warning("ignoring recording with a non-date name: %s", path.name)
            continue
        if cfg.window == "full" or session >= cutoff:
            selected.append((session, path))
    return [path for _, path in sorted(selected)]


def _gate_recorded_session_replay(sessions: list[Path]) -> tuple[GateResult, list[Any]]:
    """Verify every recording replays: hash chain, sequence continuity, schema.

    Returns the verdict and the verified snapshots, so downstream gates read the
    integrity-checked data rather than the file again.
    """
    if not sessions:
        return (
            GateResult(
                gate="recorded_session_replay",
                verdict="pending",
                detail="no recordings in the window — nothing to replay",
                metrics={"sessions": 0},
            ),
            [],
        )
    snapshots: list[Any] = []
    corrupt: list[dict[str, str]] = []
    verified = 0
    for path in sessions:
        try:
            feed = ReplayFeed.from_file(path)
        except CorruptRecordingError as exc:
            corrupt.append({"session": path.stem, "error": str(exc)})
            continue
        except OSError as exc:
            corrupt.append({"session": path.stem, "error": f"unreadable: {exc}"})
            continue
        verified += 1
        snapshots.extend(feed.replay())
    metrics = {
        "sessions": len(sessions),
        "sessions_verified": verified,
        "snapshots": len(snapshots),
        "corrupt": corrupt,
    }
    if corrupt:
        return (
            GateResult(
                gate="recorded_session_replay",
                verdict="fail",
                detail=f"{len(corrupt)} of {len(sessions)} recording(s) failed integrity checks",
                metrics=metrics,
            ),
            snapshots,
        )
    return (
        GateResult(
            gate="recorded_session_replay",
            verdict="pass",
            detail=f"{verified} recording(s), {len(snapshots)} snapshot(s) replayed clean",
            metrics=metrics,
        ),
        snapshots,
    )


def _gate_raw_market_snapshots(snapshots: list[Any]) -> GateResult:
    """Canonicalization must be uniform: one schema and normalization version.

    The cutover tolerance is *exact after canonicalization*. A window carrying
    two normalization versions cannot satisfy that — snapshots either side of
    the change are not comparable, and a parity run over them is meaningless.
    """
    if not snapshots:
        return GateResult(
            gate="raw_market_snapshots",
            verdict="pending",
            detail="no snapshots in the window",
            metrics={"snapshots": 0},
        )
    schemas = {str(s.get("schema_version")) for s in snapshots}
    normalizations = {str(s.get("normalization_version")) for s in snapshots}
    metrics = {
        "snapshots": len(snapshots),
        "schema_versions": sorted(schemas),
        "normalization_versions": sorted(normalizations),
    }
    if len(schemas) > 1 or len(normalizations) > 1:
        return GateResult(
            gate="raw_market_snapshots",
            verdict="fail",
            detail=(
                f"window mixes schema {sorted(schemas)} / normalization "
                f"{sorted(normalizations)} — snapshots are not comparable"
            ),
            metrics=metrics,
        )
    return GateResult(
        gate="raw_market_snapshots",
        verdict="pass",
        detail=f"uniform schema {sorted(schemas)[0]}, normalization {sorted(normalizations)[0]}",
        metrics=metrics,
    )


def _gate_stale_feed(snapshots: list[Any]) -> GateResult:
    """Degradation must propagate — a bad feed may never look clean.

    `spy_der.market_data.freshness` classifies fail-closed. The invariant this
    gate enforces is the downstream half: a snapshot carrying a STALE, MISSING
    or INVALID feed observation must also carry a data-quality penalty or a
    missing-component list. One that does not has silently laundered a degraded
    feed into a healthy-looking snapshot, which is the exact failure the
    fail-closed design exists to prevent.
    """
    if not snapshots:
        return GateResult(
            gate="stale_feed",
            verdict="pending",
            detail="no snapshots in the window",
            metrics={"snapshots": 0},
        )
    degraded = 0
    laundered: list[str] = []
    status_counts: dict[str, int] = {}
    for snap in snapshots:
        observations = snap.get("feed_observations") or []
        snapshot_degraded = False
        for obs in observations:
            status = str(obs.get("status", ""))
            status_counts[status] = status_counts.get(status, 0) + 1
            if status in _DEGRADED:
                snapshot_degraded = True
        if not snapshot_degraded:
            continue
        degraded += 1
        quality = snap.get("data_quality") or {}
        penalty = float(quality.get("penalty") or 0.0)
        missing = snap.get("missing_components") or []
        flags = quality.get("flags") or []
        if penalty <= 0.0 and not missing and not flags:
            laundered.append(str(snap.get("snapshot_id", "?")))
    metrics = {
        "snapshots": len(snapshots),
        "degraded_snapshots": degraded,
        "feed_status_counts": status_counts,
        "laundered_snapshot_ids": laundered[:20],
        "laundered_count": len(laundered),
    }
    if laundered:
        return GateResult(
            gate="stale_feed",
            verdict="fail",
            detail=(
                f"{len(laundered)} snapshot(s) carry a degraded feed observation "
                "with no data-quality penalty or missing-component record"
            ),
            metrics=metrics,
        )
    return GateResult(
        gate="stale_feed",
        verdict="pass",
        detail=(
            f"{degraded} degraded snapshot(s) of {len(snapshots)}; "
            "every one propagated its degradation"
        ),
        metrics=metrics,
    )


def _pending_parity_gates() -> list[GateResult]:
    return [
        GateResult(gate=name, verdict="pending", detail=reason)
        for name, reason in sorted(_PARITY_GATES_NEEDING_BOTH_RUNTIMES.items())
    ]


def run_validation(cfg: ValidationConfig, *, today: dt.date | None = None) -> dict[str, Any]:
    """Run every runnable gate over the window and build the report."""
    today = today or dt.datetime.now(ET).date()
    report_date = cfg.report_date or today.isoformat()
    sessions = _sessions_in_window(cfg, today)

    replay_gate, snapshots = _gate_recorded_session_replay(sessions)
    gates = [
        replay_gate,
        _gate_raw_market_snapshots(snapshots),
        _gate_stale_feed(snapshots),
        *_pending_parity_gates(),
    ]

    failed = [g for g in gates if g.verdict == "fail"]
    passed = [g for g in gates if g.verdict == "pass"]
    pending = [g for g in gates if g.verdict == "pending"]
    summary = (
        f"window={cfg.window} days={cfg.days} sessions={len(sessions)} "
        f"snapshots={len(snapshots)} · {len(passed)} pass, "
        f"{len(failed)} fail, {len(pending)} pending"
    )
    payload = {
        "report_date": report_date,
        "window": cfg.window,
        "days": cfg.days,
        "summary": summary,
        "ok": not failed,
        "gates": [g.to_dict() for g in gates],
        "sessions": [p.stem for p in sessions],
        "generated_at": dt.datetime.now(ET).isoformat(),
    }

    root = Path(cfg.reports_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(ET).strftime("%Y%m%d_%H%M%S")
    stamped = root / f"validation_{stamp}.json"
    atomic_write_json(stamped, payload)
    atomic_write_json(root / "latest.json", payload)
    return {
        **payload,
        "json_path": str(stamped),
        "latest_path": str(root / "latest.json"),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "SPY-DER parity validation over recorded sessions. Reports only — "
            "it never promotes and never trades."
        )
    )
    p.add_argument("--state-root", default=DEFAULT_STATE_ROOT)
    p.add_argument("--reports-dir", default=DEFAULT_REPORTS_DIR)
    p.add_argument("--window", choices=("recent", "full"), default="recent")
    p.add_argument("--days", type=int, default=5)
    p.add_argument("--report-date", default=None)
    p.add_argument("--json", action="store_true")
    # Accepted so the systemd unit's --config is not a hard error before the
    # config loader lands; the file is not read yet (same as `spy-der market`).
    p.add_argument("--config", default=None, help="reserved (not read yet)")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = build_arg_parser().parse_args(argv)
    if args.config:
        log.warning("--config is accepted but not read yet; using flags and environment")

    out = run_validation(
        ValidationConfig(
            state_root=args.state_root,
            reports_dir=args.reports_dir,
            window=args.window,
            days=max(0, args.days),
            report_date=args.report_date,
        )
    )
    print(f"\n  validation report ({out['report_date']})")
    print(f"  {out['summary']}")
    for gate in out["gates"]:
        if gate["verdict"] == "pending":
            continue
        print(f"    [{gate['verdict'].upper():4}] {gate['gate']}: {gate['detail']}")
    print(f"\n  JSON: {out['json_path']}")
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    # Fail the unit on a real gate failure; pending gates are not failures.
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
