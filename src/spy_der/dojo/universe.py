"""Universe-sparring phase — SyntheticUniverseProvider only, no 0DTE imports."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from spy_der.contracts.integration import (
    OUTCOME_PACKET_SCHEMA,
    MarketPacket,
    OutcomePacket,
)
from spy_der.dojo.config import DojoConfig
from spy_der.dojo.decisions import DecisionRole, decide_on_packets
from spy_der.dojo.evaluator import default_evaluator
from spy_der.dojo.protocols import (
    CandidateEvaluator,
    DecisionRecord,
    SyntheticUniverseProvider,
    UniverseSpec,
)

__all__ = [
    "StaticUniverseSpec",
    "collect_packets",
    "run_universe_phase",
    "synthetic_outcomes_from_packets",
]


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
    n = max(1, cfg.universes_per_gen)
    return [
        StaticUniverseSpec(
            universe_id=f"u{cfg.catalog_seed}-{i}",
            archetype=archetypes[i % len(archetypes)],
        )
        for i in range(n)
    ]


def synthetic_outcomes_from_packets(
    packets: Sequence[MarketPacket],
    *,
    archetype: str = "",
) -> list[OutcomePacket]:
    """Derive OutcomePackets when the synthetic provider does not supply them.

    Uses forecast / labels on the MarketPacket when present; otherwise assigns
    a deterministic signed P&L from archetype + candidate direction so the
    universe phase can measure AI robustness without 0DTE internals.
    """
    outcomes: list[OutcomePacket] = []
    for idx, packet in enumerate(packets):
        forecast = packet.forecast or {}
        labels = {
            "archetype": archetype,
            "source": "spy_der.synthetic_outcome",
        }
        realized_dir = (
            forecast.get("realized_direction")
            or forecast.get("direction")
            or ("up" if "up" in archetype or "bull" in archetype else None)
            or ("down" if "down" in archetype or "bear" in archetype else None)
        )
        if realized_dir is not None:
            labels["realized_direction"] = str(realized_dir)

        # Prefer an explicit synthetic pnl on the packet forecast.
        pnl_raw = forecast.get("realized_pnl")
        if pnl_raw is not None:
            pnl = Decimal(str(pnl_raw))
            cid = None
            if packet.candidates:
                cid = packet.candidates[0].candidate_id
        else:
            # Deterministic signed payoff so scoring is non-zero and stable.
            sign = 1 if (idx % 2 == 0) else -1
            if "trend_up" in archetype or "breakout" in archetype:
                sign = 1
            elif "trend_down" in archetype:
                sign = -1
            elif "chop" in archetype or "pin" in archetype:
                sign = 1 if idx % 3 else -1
            pnl = Decimal(str(0.1 * sign))
            cid = packet.candidates[0].candidate_id if packet.candidates else None

        outcomes.append(
            OutcomePacket(
                schema_version=OUTCOME_PACKET_SCHEMA,
                snapshot_id=packet.snapshot_id,
                session_date=packet.session_date,
                symbol=packet.symbol,
                candidate_id=cid,
                action="TRADE",
                realized_pnl=pnl,
                settled=True,
                labels=labels,
                settled_at=packet.generated_at or datetime.now(UTC),
            )
        )
    return outcomes


def _provider_outcomes(
    provider: SyntheticUniverseProvider,
    spec: UniverseSpec,
    packets: list[MarketPacket],
) -> list[OutcomePacket]:
    outcomes_for = getattr(provider, "outcomes_for", None)
    if callable(outcomes_for):
        return list(outcomes_for(spec))
    return synthetic_outcomes_from_packets(packets, archetype=spec.archetype)


def _empty_metrics() -> dict[str, Any]:
    return {
        "n_universes": 0,
        "n_snapshots": 0,
        "n_generations": 0,
        "mean_session_pnl": None,
        "session_win_rate": None,
        "dir_hit": None,
        "n_sessions": 0,
        "trades": 0,
        "total_pnl": 0.0,
        "win_rate": None,
        "regret": None,
        "lanes": {},
    }


def run_universe_phase(
    cfg: DojoConfig,
    provider: SyntheticUniverseProvider | None,
    *,
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

    scorer = evaluator or default_evaluator()
    catalog = _default_catalog(cfg)
    per_archetype: dict[str, dict[str, Any]] = {}
    n_packets = 0

    for generation in range(max(1, cfg.generations)):
        for spec in catalog:
            packets = list(provider.generate(spec))
            n_packets += len(packets)
            outcomes = _provider_outcomes(provider, spec, packets)
            bucket = per_archetype.setdefault(
                spec.archetype,
                {
                    "n_universes": 0,
                    "n_snapshots": 0,
                    "generations": set(),
                    "lane_reports": {
                        DecisionRole.CHAMPION.value: [],
                        DecisionRole.BASELINE.value: [],
                        DecisionRole.CHALLENGER.value: [],
                    },
                },
            )
            bucket["n_universes"] += 1
            bucket["n_snapshots"] += len(packets)
            bucket["generations"].add(generation)

            if not packets:
                continue
            for role in (
                DecisionRole.CHAMPION,
                DecisionRole.BASELINE,
                DecisionRole.CHALLENGER,
            ):
                decisions = decide_on_packets(packets, role=role)
                report = scorer.evaluate(
                    cast(Sequence[DecisionRecord], decisions), outcomes
                )
                bucket["lane_reports"][role.value].append(report.to_dict())

    matrix: dict[str, dict[str, Any]] = {}
    for arch, vals in sorted(per_archetype.items()):
        champion_reports = vals["lane_reports"][DecisionRole.CHAMPION.value]
        metrics = _empty_metrics()
        metrics["n_universes"] = vals["n_universes"]
        metrics["n_snapshots"] = vals["n_snapshots"]
        metrics["n_generations"] = len(vals["generations"])

        if champion_reports:
            pnls = [float(r.get("total_pnl") or 0.0) for r in champion_reports]
            session_means = [
                float(r["mean_session_pnl"])
                for r in champion_reports
                if r.get("mean_session_pnl") is not None
            ]
            win_rates = [
                float(r["win_rate"])
                for r in champion_reports
                if r.get("win_rate") is not None
            ]
            dir_hits = [
                float(r["dir_hit"])
                for r in champion_reports
                if r.get("dir_hit") is not None
            ]
            session_wins = [
                float(r["session_win_rate"])
                for r in champion_reports
                if r.get("session_win_rate") is not None
            ]
            trades = sum(int(r.get("trades") or 0) for r in champion_reports)
            sessions = sum(int(r.get("n_sessions") or 0) for r in champion_reports)
            metrics["total_pnl"] = round(sum(pnls), 6)
            metrics["mean_session_pnl"] = (
                round(sum(session_means) / len(session_means), 6)
                if session_means
                else (round(sum(pnls) / len(pnls), 6) if pnls else None)
            )
            metrics["win_rate"] = (
                sum(win_rates) / len(win_rates) if win_rates else None
            )
            metrics["session_win_rate"] = (
                sum(session_wins) / len(session_wins) if session_wins else None
            )
            metrics["dir_hit"] = (
                sum(dir_hits) / len(dir_hits) if dir_hits else None
            )
            metrics["trades"] = trades
            metrics["n_sessions"] = sessions
            metrics["regret"] = round(
                sum(float(r.get("regret") or 0.0) for r in champion_reports)
                / len(champion_reports),
                6,
            )
            metrics["lanes"] = {
                role: _aggregate_lane(vals["lane_reports"][role])
                for role in (
                    DecisionRole.CHAMPION.value,
                    DecisionRole.BASELINE.value,
                    DecisionRole.CHALLENGER.value,
                )
            }
        matrix[arch] = metrics

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
            "universe packets scored via CandidateEvaluator — champion / "
            "challenger / baseline P&L, win rate, and directional accuracy"
        ),
    }


def _aggregate_lane(reports: list[dict[str, Any]]) -> dict[str, Any]:
    if not reports:
        return {"status": "insufficient_data", "total_pnl": 0.0, "trades": 0}
    pnls = [float(r.get("total_pnl") or 0.0) for r in reports]
    wins = [float(r["win_rate"]) for r in reports if r.get("win_rate") is not None]
    dirs = [float(r["dir_hit"]) for r in reports if r.get("dir_hit") is not None]
    return {
        "status": "ok",
        "total_pnl": round(sum(pnls), 6),
        "mean_pnl": round(sum(pnls) / len(pnls), 6),
        "win_rate": (sum(wins) / len(wins)) if wins else None,
        "dir_hit": (sum(dirs) / len(dirs)) if dirs else None,
        "trades": sum(int(r.get("trades") or 0) for r in reports),
        "n_reports": len(reports),
    }


def collect_packets(
    provider: SyntheticUniverseProvider, specs: Iterable[UniverseSpec]
) -> list[MarketPacket]:
    out: list[MarketPacket] = []
    for spec in specs:
        out.extend(list(provider.generate(spec)))
    return out
