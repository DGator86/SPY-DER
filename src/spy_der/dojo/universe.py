"""Universe-sparring phase over SPY-DER-owned synthetic universes.

Synthetic-universe production is SPY-DER's own (:mod:`spy_der.synthetic`), so
this phase no longer degrades to ``insufficient_data`` when 0DTE is absent — it
builds a native provider and the real archetype lattice. A caller may still
inject any ``SyntheticUniverseProvider`` implementation (recorded worlds,
alternative simulators) through the ``provider`` argument.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from spy_der.contracts.integration import MarketPacket, OutcomePacket
from spy_der.decisions.shadow import reset_shadow_tick_cache
from spy_der.dojo.config import DojoConfig
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


def _catalog_for(cfg: DojoConfig, generation: int) -> UniverseCatalog:
    return UniverseCatalog(
        seed=cfg.catalog_seed,
        days=max(1, cfg.universe_days),
        generation=generation,
    )


def cfg_weights(
    cfg: DojoConfig, archetype_weights: dict[str, float] | None = None
) -> dict[str, float]:
    """Seed sampling weights for generation 0 — uniform unless told otherwise."""
    del cfg  # reserved: config-pinned priors
    if archetype_weights:
        return {a: float(archetype_weights.get(a, 1.0)) for a in ARCHETYPES}
    return dict.fromkeys(ARCHETYPES, 1.0)


def default_catalog(
    cfg: DojoConfig,
    generation: int = 0,
    *,
    archetype_weights: dict[str, float] | None = None,
) -> list[SyntheticUniverseSpec]:
    """The generation's universe specs: full lattice or a weighted sample."""
    catalog = _catalog_for(cfg, generation)
    if archetype_weights:
        catalog = replace(catalog, archetype_weights=dict(archetype_weights))
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


def _outcome_archetype(outcome: OutcomePacket) -> str | None:
    """The truth archetype the world was actually in when this tick settled.

    A universe is *named* for the archetype it starts in, but the world walks
    between archetypes as it runs — so bucketing a whole universe's P&L under
    its starting name attributes crash losses to whatever regime happened to
    open the session. The outcome labels carry the per-tick truth; use it.
    """
    label = (outcome.labels or {}).get("archetype")
    return str(label) if label else None


def _score_packets(
    packets: list[MarketPacket],
    outcomes: list[OutcomePacket],
    authorities: dict[str, DecisionAuthority],
    evaluator: CandidateEvaluator,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, dict[str, Any]]]]:
    """Score each authority over the universe, and again per truth archetype.

    Deciding is the expensive half and happens once per authority; the
    per-archetype reports are re-evaluations of those same decisions against
    outcome subsets, which is what lets the Dojo answer "does this challenger
    fix *crash*" rather than only "is it better on average".
    """
    by_archetype_outcomes: dict[str, list[OutcomePacket]] = {}
    for outcome in outcomes:
        archetype = _outcome_archetype(outcome)
        if archetype:
            by_archetype_outcomes.setdefault(archetype, []).append(outcome)

    scored: dict[str, dict[str, Any]] = {}
    per_archetype: dict[str, dict[str, dict[str, Any]]] = {}
    for name, authority in authorities.items():
        reset_shadow_tick_cache()
        decisions = [authority.decide(p) for p in packets]
        scored[name] = evaluator.evaluate(decisions, outcomes).to_dict()
        for archetype, subset in by_archetype_outcomes.items():
            report = evaluator.evaluate(decisions, subset).to_dict()
            per_archetype.setdefault(archetype, {})[name] = report
    return scored, per_archetype


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


def _matrix_from(per_archetype: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Per-archetype champion panel — also the input to the re-weighting plan."""
    matrix: dict[str, Any] = {}
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


def _merge_coverage_into(
    target: dict[str, dict[str, int]],
    source: dict[str, dict[str, int]],
) -> None:
    for archetype, rows in source.items():
        bucket = target.setdefault(archetype, {})
        for regime, minutes in rows.items():
            bucket[regime] = bucket.get(regime, 0) + int(minutes)


def run_universe_phase(
    cfg: DojoConfig,
    provider: SyntheticUniverseProvider | None = None,
    *,
    authorities: dict[str, DecisionAuthority] | None = None,
    evaluator: CandidateEvaluator | None = None,
    archetype_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Spar across synthetic worlds, biased toward where the system is weak.

    ``archetype_weights`` seeds generation 0 — the runner passes what previous
    runs remembered about weak archetypes, so training starts on the known gaps
    instead of re-discovering them. Later generations re-weight from this run's
    own scores.
    """
    if cfg.skip_universe:
        return {"status": "skipped", "note": "skip_universe"}

    active: SyntheticUniverseProvider = provider or default_provider(cfg)
    scorer: CandidateEvaluator = evaluator or OutcomeCandidateEvaluator()
    per_archetype: dict[str, dict[str, Any]] = {}
    authority_totals: dict[str, dict[str, Any]] = {}
    archetype_authorities: dict[str, dict[str, dict[str, Any]]] = {}
    coverage: dict[str, dict[str, int]] = {}
    n_packets = 0
    n_scored_universes = 0
    n_catalog_specs = 0
    weights = cfg_weights(cfg, archetype_weights)
    weights_seed = dict(weights)
    applied_plans: list[dict[str, Any]] = []

    for generation in range(max(1, cfg.generations)):
        # Each generation is drawn against the *previous* generation's scores:
        # weak archetypes and unvisited (archetype x regime) cells get more
        # draws. Without this the generations differ only by Dirichlet jitter —
        # the lattice re-measures the same distribution and the gaps it found
        # never get more training data than the parts already working.
        catalog = default_catalog(cfg, generation, archetype_weights=weights)
        n_catalog_specs += len(catalog)
        for spec in catalog:
            packets, outcomes, world_coverage = _generate(active, spec)
            _merge_coverage_into(coverage, world_coverage)
            n_packets += len(packets)
            bucket = per_archetype.setdefault(spec.archetype, _empty_bucket())
            bucket["n_universes"] += 1
            bucket["n_snapshots"] += len(packets)
            bucket["generations"].add(generation)

            if not authorities or not packets:
                continue
            if not outcomes:
                continue

            scored, scored_by_archetype = _score_packets(
                packets, outcomes, authorities, scorer
            )
            n_scored_universes += 1
            champ = scored.get("champion") or next(iter(scored.values()))
            pnl = float(champ.get("total_pnl") or 0.0)
            trades = int(champ.get("trades") or champ.get("n_matched") or 0)
            bucket["session_pnls"].append(pnl)
            bucket["trades"] += trades
            bucket["total_pnl"] += pnl
            if champ.get("win_rate") is not None and trades:
                bucket["wins"] += round(float(champ["win_rate"]) * trades)
            if champ.get("dir_hit") is not None:
                bucket["dir_hits"].append(float(champ["dir_hit"]))

            for name, report in scored.items():
                tot = authority_totals.setdefault(
                    name,
                    {"total_pnl": 0.0, "trades": 0, "n_universes": 0},
                )
                tot["total_pnl"] += float(report.get("total_pnl") or 0.0)
                tot["trades"] += int(report.get("trades") or report.get("n_matched") or 0)
                tot["n_universes"] += 1

            for archetype, reports in scored_by_archetype.items():
                slot = archetype_authorities.setdefault(archetype, {})
                for name, report in reports.items():
                    tot = slot.setdefault(
                        name, {"total_pnl": 0.0, "trades": 0, "wins": 0.0}
                    )
                    tot["total_pnl"] += float(report.get("total_pnl") or 0.0)
                    matched = int(report.get("trades") or report.get("n_matched") or 0)
                    tot["trades"] += matched
                    if report.get("win_rate") is not None and matched:
                        tot["wins"] += float(report["win_rate"]) * matched

        # Re-weight before the next generation is drawn.
        if generation + 1 < max(1, cfg.generations):
            plan = next_generation_weights(
                scores_from_archetype_matrix(_matrix_from(per_archetype)),
                merge_coverage([coverage]) if coverage else None,
                generation=generation + 1,
            )
            weights = dict(plan.weights)
            applied_plans.append(
                {
                    "generation": generation + 1,
                    "weights": {k: round(v, 4) for k, v in sorted(plan.weights.items())},
                    "targets": [
                        a
                        for a, _ in sorted(
                            plan.weakness.items(), key=lambda kv: -kv[1]
                        )[:3]
                    ],
                }
            )

    matrix = _matrix_from(per_archetype)

    # Real (archetype x regime) minute occupancy, not a lattice-cell count. The
    # previous 6*3*4 figure counted catalog coordinates, which says nothing
    # about which market states were actually visited.
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
        # What the re-weighting actually did, generation by generation. Reported
        # so "trained harder on crash" is checkable rather than asserted.
        "evolution_applied": applied_plans,
        "seed_weights": {k: round(v, 4) for k, v in sorted(weights_seed.items())},
    }
    if archetype_authorities:
        result["archetype_authorities"] = {
            archetype: {
                name: {
                    "total_pnl": round(float(tot["total_pnl"]), 6),
                    "trades": int(tot["trades"]),
                    "win_rate": (
                        round(float(tot["wins"]) / int(tot["trades"]), 6)
                        if int(tot["trades"])
                        else None
                    ),
                }
                for name, tot in sorted(reports.items())
            }
            for archetype, reports in sorted(archetype_authorities.items())
        }
    if coverage_matrix is not None:
        result["coverage"] = coverage_matrix.to_dict()
        result["coverage_cells_visited"] = coverage_matrix.visited_cells
        result["coverage_cells_total"] = coverage_matrix.total_cells
        # Where the next generation should spend its draws.
        result["evolution"] = next_generation_weights(
            scores_from_archetype_matrix(matrix),
            coverage_matrix,
            generation=max(1, cfg.generations),
        ).to_dict()
    else:
        result["coverage_cells_visited"] = 0
        result["coverage_cells_total"] = len(ARCHETYPES) * len(REGIMES)
        result["coverage_note"] = (
            "injected provider does not report world coverage; "
            "use spy_der.synthetic for the (archetype x regime) matrix"
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
        result["note"] = (
            "universe packets scored via DecisionAuthority + CandidateEvaluator"
        )
    elif n_catalog_specs > 0 and n_scored_universes == 0:
        result["status"] = "unscored"
        result["note"] = (
            f"generated {n_catalog_specs} universes / {n_packets} snapshots but "
            "scored 0 — provider outcomes missing realized_pnl labels"
        )
    else:
        result["note"] = (
            "universe packets generated via SyntheticUniverseProvider; "
            "attach DecisionAuthority + labeled outcomes for robustness P&L"
        )
    return result


def collect_packets(
    provider: SyntheticUniverseProvider, specs: Iterable[UniverseSpec]
) -> list[MarketPacket]:
    out: list[MarketPacket] = []
    for spec in specs:
        out.extend(list(provider.generate(spec)))
    return out
