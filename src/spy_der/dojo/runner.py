"""Protocol-driven Dojo runner — SPY-DER owned, no 0DTE internal imports.

Phases:
  1. recorded   — MarketExperienceProvider + DecisionAuthority scoring
  2. sequential — leak-free blind-day forward transfer / retention
  3. learner    — adaptive learning cycle (stages a challenger)
  4. universe   — SyntheticUniverseProvider sparring with AI scoring
  5. promotion  — re-run 1 and 2 with the staged change installed as the
                  candidate champion; promote it when every gate passes

Promotion is automatic and evidence-gated: a recommendation alone never moves
``champion.json``, a validated re-run does. Reports land under
/var/lib/spy-der/reports/dojo.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path as _Path
from typing import Any
from zoneinfo import ZoneInfo

from spy_der.decisions.champion import reset_champion_cache
from spy_der.decisions.shadow import ai_context
from spy_der.dojo.authority import default_authorities
from spy_der.dojo.config import DEFAULT_CONFIGS_DIR, DEFAULT_REPORTS_DIR, DojoConfig
from spy_der.dojo.evaluation import OutcomeCandidateEvaluator
from spy_der.dojo.protocols import (
    CandidateEvaluator,
    DecisionAuthority,
    MarketExperienceProvider,
    SyntheticUniverseProvider,
)
from spy_der.dojo.recorded import run_recorded_phase
from spy_der.dojo.reports import persist_dojo_report
from spy_der.dojo.sequential import SequentialDojoConfig, run_sequential_dojo
from spy_der.dojo.universe import run_universe_phase
from spy_der.learning.gaps import (
    load_archetype_gaps,
    record_archetype_gaps,
    sampling_weights,
    weakest_archetypes,
)
from spy_der.learning.learner import run_learning_cycle
from spy_der.learning.memories import append_failure_episode, append_lesson
from spy_der.learning.promotion import (
    PromotionError,
    auto_promote_pending,
    current_champion,
)
from spy_der.learning.promotion_trial import run_promotion_trial

__all__ = ["main", "run_dojo"]

ET = ZoneInfo("America/New_York")


def _run_promotion_phase(
    cfg: DojoConfig,
    *,
    learner: dict[str, Any],
    staged_changes: dict[str, Any] | None,
    experience: MarketExperienceProvider | None,
    evaluator: CandidateEvaluator,
    universe_result: dict[str, Any],
    incumbent: DecisionAuthority | None,
) -> dict[str, Any]:
    """Re-run under the staged change and promote it if the re-run validates.

    Returns the trial report either way; ``enacted`` says whether
    ``champion.json`` moved. Promotion failures are reported, never raised — a
    Dojo run must still produce its report if the config write fails.
    """
    if not cfg.auto_promote:
        return {
            "status": "disabled",
            "enacted": False,
            "note": "auto_promote disabled (SPY_DER_DOJO_AUTO_PROMOTE=0)",
        }
    if learner.get("outcome") != "promotion_recommended" or not staged_changes:
        return {
            "status": "no_candidate",
            "enacted": False,
            "note": f"learner outcome {learner.get('outcome', 'skipped')!r}",
        }

    candidate_id = learner.get("staged_candidate_id")
    trial = run_promotion_trial(
        cfg,
        changes=staged_changes,
        candidate_id=str(candidate_id) if candidate_id else None,
        experience=experience,
        evaluator=evaluator,
        universe_result=universe_result,
        current_champion=current_champion(cfg.configs_dir),
        thresholds=cfg.promotion_thresholds(),
        incumbent_authority=incumbent,
        target_archetype=(
            str(learner.get("staged_target_archetype"))
            if learner.get("staged_target_archetype")
            else None
        ),
    )
    report = trial.to_dict()
    report["enacted"] = False

    if not trial.validated or not candidate_id:
        return report

    try:
        champion_path = auto_promote_pending(
            cfg.configs_dir,
            str(candidate_id),
            validation=report,
            knobs=staged_changes,
        )
    except (PromotionError, OSError) as exc:
        report["status"] = "promotion_failed"
        report["note"] = f"validated but not written: {type(exc).__name__}: {exc}"
        return report

    # The champion the next tick reads is the one just written, not the cached one.
    reset_champion_cache()
    report["enacted"] = True
    report["champion_path"] = str(champion_path)
    report["note"] = (
        f"validated and promoted — champion.json now runs {sorted(staged_changes)}"
    )
    return report


def _build_flags(
    recorded: dict[str, Any],
    sequential: dict[str, Any],
    learner: dict[str, Any],
    universe: dict[str, Any],
    promotion: dict[str, Any] | None = None,
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
                "flag": "retention_regression",
                "detail": str(retention.get("detail", "")),
            }
        )
    promo = promotion or {}
    if promo.get("enacted"):
        flags.append(
            {
                "severity": "info",
                "flag": "champion_promoted",
                "detail": str(promo.get("note") or "promotion trial validated"),
            }
        )
    elif promo.get("status") == "rejected":
        flags.append(
            {
                "severity": "warn",
                "flag": f"promotion_rejected:{promo.get('blocking_gate') or 'gate'}",
                "detail": str(promo.get("note") or "promotion trial rejected"),
            }
        )
    elif promo.get("status") == "promotion_failed":
        flags.append(
            {
                "severity": "alert",
                "flag": "promotion_write_failed",
                "detail": str(promo.get("note") or "champion.json not written"),
            }
        )
    elif learner.get("outcome") == "promotion_recommended":
        flags.append(
            {
                "severity": "warn",
                "flag": "promotion_untried",
                "detail": str(
                    promo.get("note") or "candidate staged but no trial ran"
                ),
            }
        )
    if learner.get("outcome") == "gated":
        flags.append(
            {
                "severity": "warn",
                "flag": "staging_gated",
                "detail": str(learner.get("note", "promotion gates failed")),
            }
        )
    if universe.get("status") == "skipped" and universe.get("reason") == "no_recorded_tape":
        flags.append(
            {
                "severity": "warn",
                "flag": "universe_skipped_no_tape",
                "detail": str(
                    universe.get("note")
                    or "universe lattice refused — no recorded sessions"
                ),
            }
        )
    if universe.get("status") in {"ok", "unscored"}:
        if int(universe.get("n_scored_universes") or 0) == 0 and int(
            universe.get("n_universes") or 0
        ) > 0:
            flags.append(
                {
                    "severity": "alert",
                    "flag": "universe_unscored",
                    "detail": (
                        f"generated {universe.get('n_universes')} universes / "
                        f"{universe.get('n_snapshots')} snapshots but scored 0 — "
                        "outcomes were not attached; lattice work was wasted"
                    ),
                }
            )
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
    promoted = any(f["flag"] == "champion_promoted" for f in flags)
    rejected = next(
        (f for f in flags if f["flag"].startswith("promotion_rejected")), None
    )
    learner_state = str(learner.get("outcome", learner.get("status")))
    if promoted:
        learner_state = "promoted"
    elif rejected:
        learner_state = f"{learner_state} → rejected ({rejected['flag'].split(':')[-1]})"
    parts = [
        f"recorded tape: {recorded.get('status')}",
        f"sequential: {sequential.get('status')}",
        f"learner: {learner_state}",
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


def _persist_lessons(
    state_root: str,
    recorded: dict[str, Any],
    sequential: dict[str, Any],
    universe: dict[str, Any],
    report_date: str = "",
) -> list[str]:
    """Write AI lessons / failure episodes from scored phases."""
    written: list[str] = []
    evaluation = recorded.get("evaluation") or {}
    if evaluation.get("status") == "ok" and evaluation.get("total_pnl") is not None:
        if float(evaluation["total_pnl"]) < 0:
            lesson = append_lesson(
                state_root,
                lesson_id=f"recorded-neg-pnl-{recorded.get('n_sessions', 0)}",
                text=(
                    f"Recorded tape total P&L {evaluation['total_pnl']:+.4f} "
                    f"over {evaluation.get('trades', 0)} trades"
                ),
                tags=("recorded", "negative_pnl"),
            )
            written.append(lesson.lesson_id)
            episode = append_failure_episode(
                state_root,
                episode_id=f"fail-recorded-{recorded.get('n_sessions', 0)}",
                summary=lesson.text,
                details={
                    "phase": "recorded",
                    "evaluation": evaluation,
                    "forward_transfer": recorded.get("forward_transfer"),
                },
            )
            written.append(episode.episode_id)

    retention = sequential.get("retention") or {}
    if retention and not retention.get("ok", True):
        lesson = append_lesson(
            state_root,
            lesson_id="sequential-forgetting",
            text=str(retention.get("detail") or "retention regression"),
            tags=("sequential", "forgetting"),
        )
        written.append(lesson.lesson_id)

    # Robustness gaps are recorded as structured episodes, not only prose: the
    # next cycle reads them back as diagnoses (spy_der.learning.gaps) and trains
    # against them. A lesson is kept alongside for the human-readable trail.
    for gap in record_archetype_gaps(
        state_root, universe, report_date=str(report_date or "")
    ):
        lesson = append_lesson(
            state_root,
            lesson_id=f"weak-archetype-{gap.archetype}",
            text=(
                f"Weak synthetic archetype {gap.archetype}: mean session P&L "
                f"{gap.mean_session_pnl:+.4f} over {gap.n_sessions} session(s)"
            ),
            tags=("universe", "weak_archetype", gap.archetype),
        )
        written.append(lesson.lesson_id)
    return written


def run_dojo(
    cfg: DojoConfig | None = None,
    *,
    experience: MarketExperienceProvider | None = None,
    synthetic: SyntheticUniverseProvider | None = None,
    evaluator: CandidateEvaluator | None = None,
    authorities: dict[str, DecisionAuthority] | None = None,
    skip_sequential: bool = False,
    challenger_changes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run recorded / sequential / learner / universe with CandidateEvaluator wired.

    Runs inside :func:`ai_context` so the market-hours gate on the live decision
    path does not apply. The Dojo's timers fire at 06:30 ET, three hours before
    the open, and sparring against recorded and synthetic tape is exactly when
    the model should run — gating it would silently downgrade every Dojo run to
    the deterministic agent and change what the Dojo measures.
    """
    with ai_context("dojo"):
        return _run_dojo_phases(
            cfg,
            experience=experience,
            synthetic=synthetic,
            evaluator=evaluator,
            authorities=authorities,
            skip_sequential=skip_sequential,
            challenger_changes=challenger_changes,
        )


def _run_dojo_phases(
    cfg: DojoConfig | None = None,
    *,
    experience: MarketExperienceProvider | None = None,
    synthetic: SyntheticUniverseProvider | None = None,
    evaluator: CandidateEvaluator | None = None,
    authorities: dict[str, DecisionAuthority] | None = None,
    skip_sequential: bool = False,
    challenger_changes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg or DojoConfig()
    report_date = cfg.report_date or dt.datetime.now(ET).date().isoformat()
    cfg.report_date = report_date
    started = time.time()

    scorer: CandidateEvaluator = evaluator or OutcomeCandidateEvaluator()
    authority_set = authorities or default_authorities(
        challenger_changes=challenger_changes,
        configs_dir=cfg.configs_dir,
    )

    # What earlier runs found the system losing money in. These drive both what
    # the learner diagnoses and where the lattice spends its draws — the Dojo
    # trains on its known gaps instead of re-measuring the average.
    state_root = str(_Path(cfg.configs_dir).parent)
    remembered_gaps = load_archetype_gaps(state_root)

    recorded = run_recorded_phase(
        cfg,
        experience,
        authorities=authority_set,
        evaluator=scorer,
    )
    sequential = (
        {"status": "skipped", "note": "skip_sequential"}
        if skip_sequential
        else run_sequential_dojo(
            experience,
            cfg=SequentialDojoConfig(
                min_warm_sessions=max(2, cfg.min_sessions - 1),
            ),
            authorities=authority_set,
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
            authorities=authority_set,
            evaluator=scorer,
            sequential_result=sequential,
            recorded_result=recorded,
            weak_archetypes=weakest_archetypes(remembered_gaps),
        )
    )
    # If learner staged a challenger, re-score universe with those changes too.
    staged_changes: dict[str, Any] | None = None
    if learner.get("outcome") == "promotion_recommended":
        raw_changes = learner.get("staged_changes")
        if isinstance(raw_changes, dict) and raw_changes:
            staged_changes = dict(raw_changes)
    universe_authorities = (
        default_authorities(
            challenger_changes=staged_changes, configs_dir=cfg.configs_dir
        )
        if staged_changes
        else authority_set
    )
    # Refuse the expensive lattice when there is nothing to train against.
    # Saturday's weekly full-lattice job previously burned ~74 minutes generating
    # ~40k synthetic snapshots, scored zero of them, and wrote an empty report.
    if (
        not cfg.skip_universe
        and not cfg.force_universe
        and recorded.get("status") == "insufficient_data"
    ):
        universe = {
            "status": "skipped",
            "reason": "no_recorded_tape",
            "note": (
                "no recorded tape — refusing universe lattice "
                f"({recorded.get('note', 'insufficient_data')}); "
                "pass force_universe to override"
            ),
            "n_universes": 0,
            "n_snapshots": 0,
            "n_scored_universes": 0,
        }
    else:
        universe = run_universe_phase(
            cfg,
            synthetic,
            authorities=universe_authorities,
            evaluator=scorer,
            archetype_weights=sampling_weights(remembered_gaps),
        )

    # Phase 5 — the recommendation has to earn itself: re-run recorded tape and
    # blind days with the staged change installed, and promote it only if the
    # re-run beats the incumbent on every gate. No human in this loop.
    promotion = _run_promotion_phase(
        cfg,
        learner=learner,
        staged_changes=staged_changes,
        experience=experience,
        evaluator=scorer,
        universe_result=universe,
        incumbent=authority_set.get("champion"),
    )

    lessons = _persist_lessons(
        state_root, recorded, sequential, universe, report_date
    )

    flags = _build_flags(recorded, sequential, learner, universe, promotion)
    summary = _summary_text(recorded, sequential, learner, universe, flags)
    metrics = {
        "phases": {
            "recorded": recorded,
            "sequential": sequential,
            "learner": learner,
            "universe": universe,
            "promotion": promotion,
        },
        "training_targets": {
            "remembered_gaps": [
                {
                    "archetype": gap.archetype,
                    "mean_session_pnl": round(gap.mean_session_pnl, 6),
                    "n_sessions": gap.n_sessions,
                    "observed_at": gap.observed_at.isoformat(),
                }
                for gap in remembered_gaps
            ],
            "diagnosed": list(learner.get("diagnoses") or []),
            "targeted_archetype": learner.get("staged_target_archetype"),
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
            "authorities": sorted(authority_set.keys()),
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
            "universe training, then a promotion trial that enacts a validated "
            "change automatically."
        )
    )
    ap.add_argument("--reports-dir", default=DEFAULT_REPORTS_DIR)
    ap.add_argument("--configs-dir", default=DEFAULT_CONFIGS_DIR)
    ap.add_argument("--report-date", default=None)
    ap.add_argument("--skip-recorded", action="store_true")
    ap.add_argument("--skip-learner", action="store_true")
    ap.add_argument("--skip-universe", action="store_true")
    ap.add_argument(
        "--force-universe",
        action="store_true",
        help=(
            "Run the universe lattice even when recorded tape is insufficient. "
            "Default is to refuse — a full lattice with zero sessions is a no-op."
        ),
    )
    ap.add_argument("--skip-sequential", action="store_true")
    ap.add_argument(
        "--no-auto-promote",
        action="store_true",
        help=(
            "Stop at a staged candidate instead of re-running the system with it "
            "and promoting a validated change (same as SPY_DER_DOJO_AUTO_PROMOTE=0)."
        ),
    )
    ap.add_argument(
        "--promote-min-trades",
        type=int,
        default=20,
        help="Matched trades the candidate re-run needs before it may promote.",
    )
    ap.add_argument(
        "--promote-cooldown-hours",
        type=float,
        default=6.0,
        help="Hours between automatic promotions (three Dojo timers fire daily).",
    )
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
        force_universe=args.force_universe,
        wf_folds=args.folds,
        learn_trials=args.trials,
        universes_per_gen=args.universes,
        generations=args.generations,
        full_lattice=args.full_lattice,
        universe_days=args.days,
        catalog_seed=args.seed,
        recent_days=args.recent_days,
        promote_min_trades=args.promote_min_trades,
        promote_cooldown_hours=args.promote_cooldown_hours,
    )
    if args.no_auto_promote:
        cfg.auto_promote = False
    out = run_dojo(cfg, experience=experience, skip_sequential=args.skip_sequential)
    print(f"\n  dojo report ({out['report_date']})")
    print(f"  {out['summary']}")
    for flag in out["flags"]:
        print(f"    [{flag['severity'].upper():4}] {flag['flag']}: {flag['detail']}")
    promotion = out["metrics"]["phases"].get("promotion") or {}
    if promotion.get("status") not in {None, "no_candidate", "disabled"}:
        print(f"\n  promotion trial: {promotion.get('status')}")
        for gate in promotion.get("gates") or []:
            mark = "PASS" if gate.get("passed") else "FAIL"
            print(f"    [{mark}] {gate.get('name')}: {gate.get('detail')}")
        if promotion.get("enacted"):
            print(f"    champion: {promotion.get('champion_path')}")
    print(f"\n  JSON: {out['json_path']}")
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
