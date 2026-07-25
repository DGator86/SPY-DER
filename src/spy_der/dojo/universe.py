"""Universe-sparring phase over SPY-DER-owned synthetic universes.

Synthetic-universe production is SPY-DER's own (:mod:`spy_der.synthetic`), so
this phase no longer degrades to ``insufficient_data`` when 0DTE is absent — it
builds a native provider and the real archetype lattice. A caller may still
inject any ``SyntheticUniverseProvider`` implementation (recorded worlds,
alternative simulators) through the ``provider`` argument.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
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


def default_catalog(
    cfg: DojoConfig, generation: int = 0
) -> list[SyntheticUniverseSpec]:
    """The generation's universe specs: full lattice or a weighted sample."""
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
    per_archetype: dict[str, dict[str, Any]] = {}
    authority_totals: dict[str, dict[str, Any]] = {}
    coverage: dict[str, dict[str, int]] = {}
    n_packets = 0
    n_scored_universes = 0
    n_catalog_specs = 0

    for generation in range(max(1, cfg.generations)):
        # Each generation gets its own catalog: the generation index advances the
        # Dirichlet transition jitter, so later generations explore nearby
        # dynamics rather than replaying identical worlds.
        catalog = default_catalog(cfg, generation)
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

            scored = _score_packets(packets, outcomes, authorities, scorer)
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

    matrix = {}
    for arch, vals in sorted(per_archetype.items()):
        pnls: list[float] = list(vals["session_pnls"])
        n_sess = len(pnls)
        mean_pnl = (sum(pnls) / n_sess) if n_sess else None
        win_rate = (
            (sum(1 for p in pnls if p > 0) / n_sess) if n_sess else None
        )
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
