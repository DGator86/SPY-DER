"""Protocol-driven Dojo runner — SPY-DER owned, no 0DTE internal imports.

Phases:
  1. recorded   — MarketExperienceProvider + CandidateEvaluator lanes
  2. sequential — blind-day champion/baseline forward transfer
  3. learner    — adaptive learning cycle (stages pending_review only)
  4. universe   — SyntheticUniverseProvider sparring with real P&L

Promotion never happens here. Reports land under /var/lib/spy-der/reports/dojo.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from spy_der.dojo.config import (
    DEFAULT_CONFIGS_DIR,
    DEFAULT_REPORTS_DIR,
    DEFAULT_STATE_ROOT,
    DojoConfig,
)
from spy_der.dojo.evaluator import default_evaluator
from spy_der.dojo.protocols import (
    CandidateEvaluator,
    MarketExperienceProvider,
    SyntheticUniverseProvider,
)
from spy_der.dojo.recorded import run_recorded_phase
from spy_der.dojo.reports import persist_dojo_report
from spy_der.dojo.sequential import SequentialDojoConfig, run_sequential_dojo
from spy_der.dojo.universe import run_universe_phase
from spy_der.learning.learner import run_learning_cycle
from spy_der.learning.memories import append_failure_episode, append_lesson

__all__ = ["main", "run_dojo"]

ET = ZoneInfo("America/New_York")


def _build_flags(
    recorded: dict[str, Any],
    sequential: dict[str, Any],
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
    if sequential.get("status") == "insufficient_data":
        flags.append(
            {
                "severity": "info",
                "flag": "sequential_insufficient",
                "detail": str(sequential.get("note", "")),
            }
        )
    retention = sequential.get("retention") or {}
    if retention and not retention.get("ok", True):
        flags.append(
            {
                "severity": "warn",
                "flag": "retention_failed",
                "detail": str(retention.get("detail", "")),
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
    if learner.get("outcome") == "gates_blocked":
        flags.append(
            {
                "severity": "warn",
                "flag": "promotion_gates_blocked",
                "detail": str(learner.get("note", "complete gates not passed")),
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
    sequential: dict[str, Any],
    learner: dict[str, Any],
    universe: dict[str, Any],
    flags: list[dict[str, str]],
) -> str:
    parts = [
        f"recorded tape: {recorded.get('status')}",
        f"sequential: {sequential.get('status')}",
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
    ft = sequential.get("mean_forward_transfer")
    if ft is not None:
        parts.append(f"mean FT {float(ft):+.4f}")
    return " · ".join(parts)


def _persist_lessons(
    state_root: str,
    *,
    recorded: dict[str, Any],
    sequential: dict[str, Any],
    universe: dict[str, Any],
    flags: list[dict[str, str]],
    report_date: str,
) -> list[str]:
    """Write AI lessons and failure episodes from scored Dojo phases."""
    written: list[str] = []
    champ = (recorded.get("lanes") or {}).get("champion") or recorded.get("evaluation") or {}
    if champ.get("status") == "ok" and champ.get("win_rate") is not None:
        lesson = append_lesson(
            state_root,
            lesson_id=f"recorded-{report_date}",
            text=(
                f"Recorded champion win_rate={champ.get('win_rate'):.3f} "
                f"total_pnl={champ.get('total_pnl')} "
                f"dir_hit={champ.get('dir_hit')}"
            ),
            tags=("dojo", "recorded", "champion"),
        )
        written.append(lesson.lesson_id)

    if sequential.get("mean_forward_transfer") is not None:
        lesson = append_lesson(
            state_root,
            lesson_id=f"sequential-ft-{report_date}",
            text=(
                f"Sequential mean forward transfer="
                f"{sequential.get('mean_forward_transfer'):+.4f}; "
                f"scored={sequential.get('n_scored')}"
            ),
            tags=("dojo", "sequential", "forward_transfer"),
        )
        written.append(lesson.lesson_id)

    for flag in flags:
        if flag["severity"] != "warn":
            continue
        episode = append_failure_episode(
            state_root,
            episode_id=f"{flag['flag']}-{report_date}",
            phase="dojo",
            detail=f"{flag['flag']}: {flag['detail']}",
            metrics={"universe_status": universe.get("status")},
        )
        written.append(episode.episode_id)
    return written


def run_dojo(
    cfg: DojoConfig | None = None,
    *,
    experience: MarketExperienceProvider | None = None,
    synthetic: SyntheticUniverseProvider | None = None,
    evaluator: CandidateEvaluator | None = None,
) -> dict[str, Any]:
    """Run Dojo phases with CandidateEvaluator wired through scoring."""
    cfg = cfg or DojoConfig()
    scorer = evaluator or default_evaluator()
    report_date = cfg.report_date or dt.datetime.now(ET).date().isoformat()
    cfg.report_date = report_date
    started = time.time()
    state_root = str(Path(cfg.configs_dir).parent)

    recorded = run_recorded_phase(cfg, experience, evaluator=scorer)
    sequential = (
        {"status": "skipped", "note": "no experience provider"}
        if experience is None
        else run_sequential_dojo(
            experience,
            cfg=SequentialDojoConfig(
                min_warm_sessions=max(2, min(cfg.min_sessions - 1, 2)),
                reports_dir=cfg.reports_dir,
            ),
            evaluator=scorer,
        )
    )
    learner = (
        {"status": "skipped", "note": "skip_learner"}
        if cfg.skip_learner
        else run_learning_cycle(
            mode="dojo",
            configs_dir=cfg.configs_dir,
            experience=experience,
            trials=cfg.learn_trials,
            holdout=cfg.learn_holdout,
            evaluator=scorer,
            sequential_summary=sequential,
            recorded_summary=recorded,
        )
    )
    universe = run_universe_phase(cfg, synthetic, evaluator=scorer)

    flags = _build_flags(recorded, sequential, learner, universe)
    summary = _summary_text(recorded, sequential, learner, universe, flags)
    lessons = _persist_lessons(
        state_root if state_root != "/" else DEFAULT_STATE_ROOT,
        recorded=recorded,
        sequential=sequential,
        universe=universe,
        flags=flags,
        report_date=report_date,
    )
    metrics = {
        "phases": {
            "recorded": recorded,
            "sequential": sequential,
            "learner": learner,
            "universe": universe,
        },
        "lessons_written": lessons,
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
            "SPY-DER Dojo — protocol-driven recorded / sequential / learner / "
            "universe training. Never auto-promotes."
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
