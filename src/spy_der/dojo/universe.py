"""Universe-sparring phase — SyntheticUniverseProvider only, no 0DTE imports."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from spy_der.contracts.integration import MarketPacket
from spy_der.dojo.config import DojoConfig
from spy_der.dojo.protocols import SyntheticUniverseProvider, UniverseSpec

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


def run_universe_phase(
    cfg: DojoConfig,
    provider: SyntheticUniverseProvider | None,
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
    per_archetype: dict[str, dict[str, Any]] = {}
    n_packets = 0
    for generation in range(max(1, cfg.generations)):
        for spec in catalog:
            packets: list[MarketPacket] = list(provider.generate(spec))
            n_packets += len(packets)
            bucket = per_archetype.setdefault(
                spec.archetype,
                {
                    "n_universes": 0,
                    "n_snapshots": 0,
                    "generations": set(),
                },
            )
            bucket["n_universes"] += 1
            bucket["n_snapshots"] += len(packets)
            bucket["generations"].add(generation)

    matrix = {
        arch: {
            "n_universes": vals["n_universes"],
            "n_snapshots": vals["n_snapshots"],
            "n_generations": len(vals["generations"]),
            # Scoring requires CandidateEvaluator + decisions; left null until wired.
            "mean_session_pnl": None,
            "session_win_rate": None,
            "dir_hit": None,
            "n_sessions": 0,
            "trades": 0,
            "total_pnl": 0.0,
        }
        for arch, vals in sorted(per_archetype.items())
    }
    return {
        "status": "ok",
        "n_universes": len(catalog) * max(1, cfg.generations),
        "n_snapshots": n_packets,
        "generations": cfg.generations,
        "full_lattice": cfg.full_lattice,
        "universe_days": cfg.universe_days,
        "archetype_matrix": matrix,
        "coverage_cells_visited": len(matrix),
        "coverage_cells_total": 6 * 3 * 4 if cfg.full_lattice else len(matrix),
        "note": (
            "universe packets generated via SyntheticUniverseProvider; "
            "robustness P&L fills in when CandidateEvaluator is attached"
        ),
    }


def collect_packets(
    provider: SyntheticUniverseProvider, specs: Iterable[UniverseSpec]
) -> list[MarketPacket]:
    out: list[MarketPacket] = []
    for spec in specs:
        out.extend(list(provider.generate(spec)))
    return out
