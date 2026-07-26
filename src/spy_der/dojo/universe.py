"""Universe-sparring phase over SPY-DER-owned synthetic universes.

Synthetic-universe production is SPY-DER's own (:mod:`spy_der.synthetic`), so
this phase no longer degrades to ``insufficient_data`` when 0DTE is absent — it
builds a native provider and the real archetype lattice. A caller may still
inject any ``SyntheticUniverseProvider`` implementation (recorded worlds,
alternative simulators) through the ``provider`` argument.

Generations re-weight toward weak / unvisited archetypes via
:mod:`spy_der.synthetic.evolution`. Immediate next-draw weights come from
**generation-local** scores; the cumulative matrix is kept for the robustness
report. Curriculum inertia blends each new plan with the prior so a weekly
full-lattice measurement cannot erase accumulated gap pressure. Final weights
persist under ``configs/curriculum_weights.json``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from spy_der.contracts.integration import MarketPacket, OutcomePacket
from spy_der.decisions.shadow import reset_shadow_tick_cache
from spy_der.dojo.config import DojoConfig
from spy_der.dojo.curriculum_weights import (
    load_curriculum_weights,
    save_curriculum_weights,
)
from spy_der.dojo.evaluation import OutcomeCandidateEvaluator, forward_transfer
from spy_der.dojo.outcomes import outcomes_from_packets
from spy_der.dojo.protocols import (
    CandidateEvaluator,
    DecisionAuthority,
    SyntheticUniverseProvider,
    UniverseSpec,
)
from spy_der.synthetic.archetypes import ARCHETYPES, REGIMES, simulator_config_hash
from spy_der.synthetic.evolution import (
    CURRICULUM_INERTIA,
    EvolutionPlan,
    evolve_catalog,
    focus_from_plan,
    next_generation_weights,
    scores_from_archetype_matrix,
)
from spy_der.synthetic.provider import (
    SyntheticUniverseProvider as NativeSyntheticUniverseProvider,
)
from spy_der.synthetic.universe import UniverseCatalog, merge_coverage
from spy_der.synthetic.universe import UniverseSpec as SyntheticUniverseSpec

__all__ = [
    "StaticUniverseSpec",
    "default_catalog",
    "default_provider",
    "run_universe_phase",
]

#: Human labels for archetype ids shown in reports / dashboard copy.
_ARCHETYPE_LABELS: dict[str, str] = {
    "calm_pin": "calm pin",
    "grind_up": "grind up",
    "grind_down": "grind down",
    "range_chop": "range chop",
    "vol_expansion": "vol expansion",
    "squeeze_melt_up": "squeeze melt-up",
    "crash": "crash",
    "gap_shock": "gap shock",
}


@dataclass(frozen=True, slots=True)
class StaticUniverseSpec:
    """Legacy string-coordinate spec.

    Superseded by :class:`spy_der.synthetic.UniverseSpec`, whose archetype names
    are the real ones the simulator generates. Retained so existing callers and
    fixtures keep importing; new code should use the synthetic spec.
    """

    universe_id: str
    archetype: str
    tilt: str = "neutral"
    vol: str = "mid"


def _catalog_for(
    cfg: DojoConfig,
    generation: int = 0,
    *,
    archetype_weights: dict[str, float] | None = None,
) -> UniverseCatalog:
    kwargs: dict[str, Any] = {
        "seed": cfg.catalog_seed,
        "days": max(1, cfg.universe_days),
        "generation": generation,
    }
    if archetype_weights is not None:
        kwargs["archetype_weights"] = dict(archetype_weights)
    return UniverseCatalog(**kwargs)


def default_catalog(
    cfg: DojoConfig, generation: int = 0
) -> list[SyntheticUniverseSpec]:
    """The generation's universe specs: full lattice or a weighted sample.

    Prefer :func:`run_universe_phase`, which keeps a live catalog and evolves
    weights between generations. This helper remains for callers that only need
    a one-shot sample.
    """
    catalog = _catalog_for(cfg, generation)
    if cfg.full_lattice:
        return catalog.full_lattice()
    return catalog.sample(max(1, cfg.universes_per_gen))


def default_provider(cfg: DojoConfig | None = None) -> NativeSyntheticUniverseProvider:
    """SPY-DER's native synthetic-universe provider, tuned by ``cfg``."""
    stride = cfg.universe_snapshot_stride if cfg is not None else 15
    return NativeSyntheticUniverseProvider(snapshot_stride=max(1, stride))


def _empty_bucket() -> dict[str, Any]:
    return {
        "n_universes": 0,
        "n_snapshots": 0,
        "generations": set(),
        "session_pnls": [],
        "trades": 0,
        "total_pnl": 0.0,
        "dir_hits": [],
        "wins": 0,
    }


def _score_packets(
    packets: list[MarketPacket],
    outcomes: list[OutcomePacket],
    authorities: dict[str, DecisionAuthority],
    evaluator: CandidateEvaluator,
) -> dict[str, dict[str, Any]]:
    scored: dict[str, dict[str, Any]] = {}
    for name, authority in authorities.items():
        reset_shadow_tick_cache()
        decisions = [authority.decide(p) for p in packets]
        report = evaluator.evaluate(decisions, outcomes)
        scored[name] = report.to_dict()
    return scored


def _generate(
    provider: SyntheticUniverseProvider,
    spec: Any,
) -> tuple[list[MarketPacket], list[OutcomePacket], dict[str, dict[str, int]]]:
    """Generate a universe, keeping outcomes + coverage when the provider has them.

    The native provider's ``generate_result`` returns packets, ground-truth
    outcomes, and ``(archetype, regime)`` occupancy together. Discarding the
    outcomes (the previous bug) meant the lattice generated tens of thousands of
    snapshots and then scored none of them.
    """
    result_fn = getattr(provider, "generate_result", None)
    if callable(result_fn):
        result = result_fn(spec)
        packets = list(result.packets)
        outcomes = list(getattr(result, "outcomes", ()) or ())
        coverage = dict(getattr(result, "coverage", {}) or {})
        if not outcomes:
            outcomes = outcomes_from_packets(packets)
        return packets, outcomes, coverage
    packets = list(provider.generate(spec))
    return packets, outcomes_from_packets(packets), {}


def _merge_coverage_into(
    target: dict[str, dict[str, int]],
    source: dict[str, dict[str, int]],
) -> None:
    for archetype, rows in source.items():
        bucket = target.setdefault(archetype, {})
        for regime, minutes in rows.items():
            bucket[regime] = bucket.get(regime, 0) + int(minutes)


def _matrix_from_buckets(
    per_archetype: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    matrix: dict[str, dict[str, Any]] = {}
    for arch, vals in sorted(per_archetype.items()):
        pnls: list[float] = list(vals["session_pnls"])
        n_sess = len(pnls)
        mean_pnl = (sum(pnls) / n_sess) if n_sess else None
        win_rate = (sum(1 for p in pnls if p > 0) / n_sess) if n_sess else None
        dir_hits: list[float] = list(vals["dir_hits"])
        dir_hit = (sum(dir_hits) / len(dir_hits)) if dir_hits else None
        matrix[arch] = {
            "n_universes": vals["n_universes"],
            "n_snapshots": vals["n_snapshots"],
            "n_generations": len(vals["generations"]),
            "mean_session_pnl": (
                round(mean_pnl, 6) if mean_pnl is not None else None
            ),
            "session_win_rate": win_rate,
            "dir_hit": dir_hit,
            "n_sessions": n_sess,
            "trades": vals["trades"],
            "total_pnl": round(float(vals["total_pnl"]), 6),
        }
    return matrix


def _specs_for_generation(
    cfg: DojoConfig,
    catalog: UniverseCatalog,
    *,
    generation: int,
) -> list[SyntheticUniverseSpec]:
    """Full lattice only measures gen 0; later gens remediate via weighted sample."""
    if cfg.full_lattice and generation == 0:
        return catalog.full_lattice()
    return catalog.sample(max(1, cfg.universes_per_gen))


def _losing_archetypes(
    matrix: dict[str, dict[str, Any]],
    *,
    min_sessions: int = 1,
) -> list[dict[str, Any]]:
    """Negative-P&L rows for flags / lessons (not the evolution ranking)."""
    weak: list[dict[str, Any]] = []
    for arch, metrics in matrix.items():
        mean = metrics.get("mean_session_pnl")
        n_sess = int(metrics.get("n_sessions") or 0)
        if n_sess < min_sessions or mean is None or float(mean) >= 0.0:
            continue
        weak.append(
            {
                "archetype": arch,
                "label": _ARCHETYPE_LABELS.get(arch, arch.replace("_", " ")),
                "mean_session_pnl": float(mean),
                "session_win_rate": metrics.get("session_win_rate"),
                "n_sessions": n_sess,
            }
        )
    weak.sort(key=lambda row: row["mean_session_pnl"])
    return weak


def _remediation_block(
    plan: EvolutionPlan | None,
    *,
    scores: dict[str, Any],
    cumulative_matrix: dict[str, dict[str, Any]],
    prior_weights: dict[str, float] | None,
    prior_influenced_sampling: bool,
    prior_blended_into_plan: bool,
) -> dict[str, Any]:
    """Remediation copy mirrors the evolution plan ranking, with reasons."""
    focus = focus_from_plan(plan, scores, top_n=3) if plan is not None else []
    losing = _losing_archetypes(cumulative_matrix, min_sessions=1)

    labels = [row["label"] for row in focus]
    if labels:
        headline = f"Next sparring will focus on {', '.join(labels)}."
    else:
        headline = "No elevated sampling targets in this panel."

    if prior_influenced_sampling:
        prior_note = "This run sampled with last night's gap weights."
    elif prior_blended_into_plan:
        prior_note = (
            "Full-lattice measurement ignores sampling weights; prior curriculum "
            "was blended into the next-run plan (inertia)."
        )
    elif prior_weights is not None:
        prior_note = "Prior curriculum weights were loaded."
    else:
        prior_note = None

    unvisited = list(plan.unvisited_cells) if plan is not None else []
    return {
        "headline": headline,
        "focus": focus,
        "weak_archetypes": losing,
        "unvisited_cells": [list(cell) for cell in unvisited[:12]],
        "unvisited_count": len(unvisited),
        "seeded_from_prior": prior_weights is not None,
        "prior_influenced_sampling": prior_influenced_sampling,
        "prior_blended_into_plan": prior_blended_into_plan,
        "prior_note": prior_note,
        "inertia": plan.inertia if plan is not None else 0.0,
        "next_generation": plan.generation if plan is not None else None,
    }


def run_universe_phase(
    cfg: DojoConfig,
    provider: SyntheticUniverseProvider | None = None,
    *,
    authorities: dict[str, DecisionAuthority] | None = None,
    evaluator: CandidateEvaluator | None = None,
) -> dict[str, Any]:
    if cfg.skip_universe:
        return {"status": "skipped", "note": "skip_universe"}

    active: SyntheticUniverseProvider = provider or default_provider(cfg)
    scorer: CandidateEvaluator = evaluator or OutcomeCandidateEvaluator()
    # Cumulative across the whole phase — robustness matrix / flags / lessons.
    per_archetype: dict[str, dict[str, Any]] = {}
    authority_totals: dict[str, dict[str, Any]] = {}
    coverage: dict[str, dict[str, int]] = {}
    n_packets = 0
    n_scored_universes = 0
    n_catalog_specs = 0
    generation_plans: list[dict[str, Any]] = []
    # Last generation-local scores — used so remediation reasons match the plan.
    latest_gen_scores: dict[str, Any] = {}

    prior_weights = load_curriculum_weights(cfg.configs_dir)
    catalog = _catalog_for(
        cfg,
        generation=0,
        archetype_weights=prior_weights,
    )
    latest_plan: EvolutionPlan | None = None
    prior_influenced_sampling = False
    prior_blended_into_plan = False

    n_generations = max(1, cfg.generations)
    for generation in range(n_generations):
        # Gen-local buckets: next-draw evolution must not be dominated by a
        # large full-lattice gen 0 once remediation samples start landing.
        gen_buckets: dict[str, dict[str, Any]] = {}

        # Gen 0 of a full-lattice run measures every cell; later generations
        # switch to a weighted sample so remediation concentrates on gaps.
        specs = _specs_for_generation(cfg, catalog, generation=generation)
        if not (cfg.full_lattice and generation == 0) and prior_weights is not None:
            # Sampling path used catalog weights seeded from disk or prior gen.
            prior_influenced_sampling = True
        n_catalog_specs += len(specs)
        for spec in specs:
            packets, outcomes, world_coverage = _generate(active, spec)
            _merge_coverage_into(coverage, world_coverage)
            n_packets += len(packets)
            cum_bucket = per_archetype.setdefault(spec.archetype, _empty_bucket())
            gen_bucket = gen_buckets.setdefault(spec.archetype, _empty_bucket())
            cum_bucket["n_universes"] += 1
            cum_bucket["n_snapshots"] += len(packets)
            cum_bucket["generations"].add(generation)
            gen_bucket["n_universes"] += 1
            gen_bucket["n_snapshots"] += len(packets)
            gen_bucket["generations"].add(generation)

            if not authorities or not packets:
                continue
            if not outcomes:
                continue

            scored = _score_packets(packets, outcomes, authorities, scorer)
            n_scored_universes += 1
            champ = scored.get("champion") or next(iter(scored.values()))
            pnl = float(champ.get("total_pnl") or 0.0)
            trades = int(champ.get("trades") or champ.get("n_matched") or 0)
            win_rate = float(champ["win_rate"]) if champ.get("win_rate") is not None else None
            dir_hit = float(champ["dir_hit"]) if champ.get("dir_hit") is not None else None

            for bucket in (cum_bucket, gen_bucket):
                bucket["session_pnls"].append(pnl)
                bucket["trades"] += trades
                bucket["total_pnl"] += pnl
                if win_rate is not None and trades:
                    bucket["wins"] += round(win_rate * trades)
                if dir_hit is not None:
                    bucket["dir_hits"].append(dir_hit)

            for name, report in scored.items():
                tot = authority_totals.setdefault(
                    name,
                    {"total_pnl": 0.0, "trades": 0, "n_universes": 0},
                )
                tot["total_pnl"] += float(report.get("total_pnl") or 0.0)
                tot["trades"] += int(
                    report.get("trades") or report.get("n_matched") or 0
                )
                tot["n_universes"] += 1

        # Coverage stays cumulative (unvisited means never seen this run).
        # Performance scores for the next draw are generation-local.
        gen_matrix = _matrix_from_buckets(gen_buckets)
        coverage_matrix = merge_coverage([coverage]) if coverage else None
        gen_scores = scores_from_archetype_matrix(gen_matrix)
        latest_gen_scores = gen_scores

        if generation + 1 < n_generations:
            catalog, latest_plan = evolve_catalog(
                catalog,
                gen_scores,
                coverage_matrix,
                prior_weights=catalog.archetype_weights,
                inertia=CURRICULUM_INERTIA,
            )
            generation_plans.append(latest_plan.to_dict())
        else:
            latest_plan = next_generation_weights(
                gen_scores,
                coverage_matrix,
                generation=catalog.generation + 1,
                prior_weights=catalog.archetype_weights,
                inertia=CURRICULUM_INERTIA,
            )
            generation_plans.append(latest_plan.to_dict())

        # Only credit "prior curriculum" when disk weights were in the blend chain.
        if (
            prior_weights is not None
            and latest_plan is not None
            and latest_plan.blended_from_prior
        ):
            prior_blended_into_plan = True

    matrix = _matrix_from_buckets(per_archetype)
    coverage_matrix = merge_coverage([coverage]) if coverage else None

    result: dict[str, Any] = {
        "status": "ok",
        "n_universes": n_catalog_specs,
        "n_snapshots": n_packets,
        "n_scored_universes": n_scored_universes,
        "generations": cfg.generations,
        "full_lattice": cfg.full_lattice,
        "universe_days": cfg.universe_days,
        "simulator_config_hash": simulator_config_hash(),
        "archetype_matrix": matrix,
        "seeded_from_prior_weights": prior_weights is not None,
        "prior_influenced_sampling": prior_influenced_sampling,
        "prior_blended_into_plan": prior_blended_into_plan,
        "generation_plans": generation_plans,
        "curriculum_inertia": CURRICULUM_INERTIA,
    }
    if coverage_matrix is not None:
        result["coverage"] = coverage_matrix.to_dict()
        result["coverage_cells_visited"] = coverage_matrix.visited_cells
        result["coverage_cells_total"] = coverage_matrix.total_cells
    else:
        result["coverage_cells_visited"] = 0
        result["coverage_cells_total"] = len(ARCHETYPES) * len(REGIMES)
        result["coverage_note"] = (
            "injected provider does not report world coverage; "
            "use spy_der.synthetic for the (archetype x regime) matrix"
        )

    if latest_plan is not None:
        result["evolution"] = latest_plan.to_dict()
        losing = _losing_archetypes(matrix, min_sessions=1)
        saved = save_curriculum_weights(
            cfg.configs_dir,
            weights=latest_plan.weights,
            generation=latest_plan.generation,
            weak_archetypes=[row["archetype"] for row in losing],
            extra={
                "report_status": result["status"],
                "inertia": latest_plan.inertia,
                "blended_from_prior": latest_plan.blended_from_prior,
            },
        )
        if saved is not None:
            result["curriculum_weights_path"] = str(saved)

    result["remediation"] = _remediation_block(
        latest_plan,
        scores=latest_gen_scores,
        cumulative_matrix=matrix,
        prior_weights=prior_weights,
        prior_influenced_sampling=prior_influenced_sampling,
        prior_blended_into_plan=prior_blended_into_plan,
    )

    if authority_totals:
        result["authorities"] = authority_totals
        champ_tot = authority_totals.get("champion")
        base_tot = authority_totals.get("baseline")
        if champ_tot is not None and base_tot is not None:
            result["forward_transfer"] = forward_transfer(
                float(champ_tot["total_pnl"]),
                float(base_tot["total_pnl"]),
            )
        result["note"] = "Synthetic worlds scored against the live decision path."
    elif n_catalog_specs > 0 and n_scored_universes == 0:
        result["status"] = "unscored"
        result["note"] = (
            f"generated {n_catalog_specs} universes / {n_packets} snapshots but "
            "scored 0 — provider outcomes missing realized_pnl labels"
        )
    else:
        result["note"] = (
            "Synthetic worlds generated; attach a decision authority with "
            "labeled outcomes to score robustness P&L."
        )
    return result


def collect_packets(
    provider: SyntheticUniverseProvider, specs: Iterable[UniverseSpec]
) -> list[MarketPacket]:
    out: list[MarketPacket] = []
    for spec in specs:
        out.extend(list(provider.generate(spec)))
    return out
