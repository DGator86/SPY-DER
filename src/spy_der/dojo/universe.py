"""Universe-sparring phase — SyntheticUniverseProvider only, no 0DTE imports."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from spy_der.contracts.integration import MarketPacket, OutcomePacket
from spy_der.dojo.config import DojoConfig
from spy_der.dojo.evaluation import OutcomeCandidateEvaluator, forward_transfer
from spy_der.dojo.outcomes import outcomes_from_packets
from spy_der.dojo.protocols import (
    CandidateEvaluator,
    DecisionAuthority,
    SyntheticUniverseProvider,
    UniverseSpec,
)
from spy_der.integrations.zerodte.provider import reset_shadow_tick_cache

__all__ = ["StaticUniverseSpec", "run_universe_phase"]


@dataclass(frozen=True, slots=True)
class StaticUniverseSpec:
    universe_id: str
    archetype: str
    tilt: str = "neutral"
    vol: str = "mid"


def _default_catalog(cfg: DojoConfig) -> list[StaticUniverseSpec]:
    archetypes = (
        "trend_up",
        "trend_down",
        "mean_revert",
        "chop",
        "breakout",
        "pin",
    )
    if cfg.full_lattice:
        tilts = ("bull", "neutral", "bear")
        vols = ("low", "mid", "high", "spike")
        specs: list[StaticUniverseSpec] = []
        idx = 0
        for arch in archetypes:
            for tilt in tilts:
                for vol in vols:
                    specs.append(
                        StaticUniverseSpec(
                            universe_id=f"u{cfg.catalog_seed}-{idx}",
                            archetype=arch,
                            tilt=tilt,
                            vol=vol,
                        )
                    )
                    idx += 1
        return specs
    # Weighted shallow sample — deterministic from seed.
    n = max(1, cfg.universes_per_gen)
    return [
        StaticUniverseSpec(
            universe_id=f"u{cfg.catalog_seed}-{i}",
            archetype=archetypes[i % len(archetypes)],
        )
        for i in range(n)
    ]


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


def run_universe_phase(
    cfg: DojoConfig,
    provider: SyntheticUniverseProvider | None,
    *,
    authorities: dict[str, DecisionAuthority] | None = None,
    evaluator: CandidateEvaluator | None = None,
) -> dict[str, Any]:
    if cfg.skip_universe:
        return {"status": "skipped", "note": "skip_universe"}
    if provider is None:
        return {
            "status": "insufficient_data",
            "note": (
                "no SyntheticUniverseProvider — 0DTE must expose synthetic "
                "market snapshots via the integration contract"
            ),
        }

    catalog = _default_catalog(cfg)
    scorer: CandidateEvaluator = evaluator or OutcomeCandidateEvaluator()
    per_archetype: dict[str, dict[str, Any]] = {}
    authority_totals: dict[str, dict[str, Any]] = {}
    n_packets = 0
    n_scored_universes = 0

    for generation in range(max(1, cfg.generations)):
        for spec in catalog:
            packets: list[MarketPacket] = list(provider.generate(spec))
            n_packets += len(packets)
            bucket = per_archetype.setdefault(spec.archetype, _empty_bucket())
            bucket["n_universes"] += 1
            bucket["n_snapshots"] += len(packets)
            bucket["generations"].add(generation)

            if not authorities or not packets:
                continue

            outcomes = outcomes_from_packets(packets)
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

    result: dict[str, Any] = {
        "status": "ok",
        "n_universes": len(catalog) * max(1, cfg.generations),
        "n_snapshots": n_packets,
        "n_scored_universes": n_scored_universes,
        "generations": cfg.generations,
        "full_lattice": cfg.full_lattice,
        "universe_days": cfg.universe_days,
        "archetype_matrix": matrix,
        "coverage_cells_visited": len(matrix),
        "coverage_cells_total": 6 * 3 * 4 if cfg.full_lattice else len(matrix),
    }
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
