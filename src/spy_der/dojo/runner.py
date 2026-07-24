"""Protocol-driven Dojo runner — SPY-DER owned, no 0DTE internal imports.

Phases:
  1. recorded  — MarketExperienceProvider baseline
  2. learner   — adaptive learning cycle (stages pending_review only)
  3. universe  — SyntheticUniverseProvider sparring

Promotion never happens here. Reports land under /var/lib/spy-der/reports/dojo.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from typing import Any
from zoneinfo import ZoneInfo

from spy_der.dojo.config import DEFAULT_CONFIGS_DIR, DEFAULT_REPORTS_DIR, DojoConfig
from spy_der.dojo.protocols import (
    CandidateEvaluator,
    MarketExperienceProvider,
    SyntheticUniverseProvider,
)
from spy_der.dojo.recorded import run_recorded_phase
from spy_der.dojo.reports import persist_dojo_report
from spy_der.dojo.universe import run_universe_phase
from spy_der.learning.learner import run_learning_cycle

__all__ = ["main", "run_dojo"]

ET = ZoneInfo("America/New_York")


def _build_flags(
    recorded: dict[str, Any],
    learner: dict[str, Any],
    universe: dict[str, Any],
) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    if recorded.get("status") == "insufficient_data":
        flags.append(
            {
                "severity": "info",
                "flag": "no_recorded_tape",
                "detail": str(recorded.get("note", "")),
            }
        )
    if learner.get("outcome") == "promotion_recommended":
        flags.append(
            {
                "severity": "info",
                "flag": "promotion_pending_review",
                "detail": "learner staged a candidate — human promoter required",
            }
        )
    if universe.get("status") == "insufficient_data":
        flags.append(
            {
                "severity": "info",
                "flag": "universe_provider_missing",
                "detail": str(universe.get("note", "")),
            }
        )
    if universe.get("status") == "ok":
        for arch, metrics in (universe.get("archetype_matrix") or {}).items():
            mean = metrics.get("mean_session_pnl")
            if (
                metrics.get("n_sessions", 0) >= 3
                and mean is not None
                and float(mean) < 0
            ):
                flags.append(
                    {
                        "severity": "warn",
                        "flag": f"weak_archetype:{arch}",
                        "detail": f"mean session P&L {float(mean):+.4f}",
                    }
                )
    return flags


def _summary_text(
    recorded: dict[str, Any],
    learner: dict[str, Any],
    universe: dict[str, Any],
    flags: list[dict[str, str]],
) -> str:
    parts = [
        f"recorded tape: {recorded.get('status')}",
        f"learner: {learner.get('outcome', learner.get('status'))}",
    ]
    if universe.get("status") == "ok":
        weak = sum(1 for f in flags if f["flag"].startswith("weak_archetype"))
        parts.append(
            f"universe sparring: {universe.get('n_universes', 0)} universes, "
            f"{weak} weak archetype(s)"
        )
    else:
        parts.append(f"universe sparring: {universe.get('status')}")
    return " · ".join(parts)


def run_dojo(
    cfg: DojoConfig | None = None,
    *,
    experience: MarketExperienceProvider | None = None,
    synthetic: SyntheticUniverseProvider | None = None,
    evaluator: CandidateEvaluator | None = None,
) -> dict[str, Any]:
    """Run the three Dojo phases. ``evaluator`` is reserved for Phase-4 wiring."""
    del evaluator  # attached in a later phase when 0DTE backtest adapter lands
    cfg = cfg or DojoConfig()
    report_date = cfg.report_date or dt.datetime.now(ET).date().isoformat()
    cfg.report_date = report_date
    started = time.time()

    recorded = run_recorded_phase(cfg, experience)
    learner = (
        {"status": "skipped", "note": "skip_learner"}
        if cfg.skip_learner
        else run_learning_cycle(
            mode="dojo",
            configs_dir=cfg.configs_dir,
            experience=experience,
            trials=cfg.learn_trials,
            holdout=cfg.learn_holdout,
        )
    )
    universe = run_universe_phase(cfg, synthetic)

    flags = _build_flags(recorded, learner, universe)
    summary = _summary_text(recorded, learner, universe, flags)
    metrics = {
        "phases": {
            "recorded": recorded,
            "learner": learner,
            "universe": universe,
        },
        "elapsed_s": round(time.time() - started, 1),
        "config": {
            "wf_folds": cfg.wf_folds,
            "learn_trials": cfg.learn_trials,
            "universes_per_gen": cfg.universes_per_gen,
            "generations": cfg.generations,
            "full_lattice": cfg.full_lattice,
            "universe_days": cfg.universe_days,
            "recent_days": cfg.recent_days,
            "catalog_seed": cfg.catalog_seed,
        },
    }
    paths = persist_dojo_report(
        cfg.reports_dir,
        report_date=report_date,
        summary=summary,
        flags=flags,
        metrics=metrics,
    )
    return {
        "report_date": report_date,
        "summary": summary,
        "flags": flags,
        "metrics": metrics,
        **paths,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "SPY-DER Dojo — protocol-driven recorded / learner / universe "
            "training. Never auto-promotes."
        )
    )
    ap.add_argument("--reports-dir", default=DEFAULT_REPORTS_DIR)
    ap.add_argument("--configs-dir", default=DEFAULT_CONFIGS_DIR)
    ap.add_argument("--report-date", default=None)
    ap.add_argument("--skip-recorded", action="store_true")
    ap.add_argument("--skip-learner", action="store_true")
    ap.add_argument("--skip-universe", action="store_true")
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--trials", type=int, default=15)
    ap.add_argument("--universes", type=int, default=6)
    ap.add_argument("--generations", type=int, default=2)
    ap.add_argument("--full-lattice", action="store_true")
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20260723)
    ap.add_argument("--recent-days", type=int, default=0)
    ap.add_argument("--experience-dir", default="", help="directory of MarketPacket JSON")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    experience = None
    if args.experience_dir:
        from spy_der.integrations.zerodte.recorded_feed import FileMarketExperienceProvider

        experience = FileMarketExperienceProvider(args.experience_dir)

    cfg = DojoConfig(
        reports_dir=args.reports_dir,
        configs_dir=args.configs_dir,
        report_date=args.report_date,
        skip_recorded=args.skip_recorded,
        skip_learner=args.skip_learner,
        skip_universe=args.skip_universe,
        wf_folds=args.folds,
        learn_trials=args.trials,
        universes_per_gen=args.universes,
        generations=args.generations,
        full_lattice=args.full_lattice,
        universe_days=args.days,
        catalog_seed=args.seed,
        recent_days=args.recent_days,
    )
    out = run_dojo(cfg, experience=experience)
    print(f"\n  dojo report ({out['report_date']})")
    print(f"  {out['summary']}")
    for flag in out["flags"]:
        print(f"    [{flag['severity'].upper():4}] {flag['flag']}: {flag['detail']}")
    print(f"\n  JSON: {out['json_path']}")
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
