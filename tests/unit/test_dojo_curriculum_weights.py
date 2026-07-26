"""Curriculum weights persist across Dojo runs and steer universe sampling."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from spy_der.agents.mock import MockDecisionAgent
from spy_der.contracts.agents import AgentEntryAction
from spy_der.contracts.integration import (
    MARKET_PACKET_SCHEMA,
    OUTCOME_PACKET_SCHEMA,
    MarketCandidateView,
    MarketPacket,
    OutcomePacket,
)
from spy_der.dojo.authority import ActiveDecisionAuthority
from spy_der.dojo.config import DojoConfig
from spy_der.dojo.curriculum_weights import (
    CURRICULUM_WEIGHTS_FILENAME,
    load_curriculum_weights,
    save_curriculum_weights,
)
from spy_der.dojo.evaluation import OutcomeCandidateEvaluator
from spy_der.dojo.universe import run_universe_phase
from spy_der.synthetic.archetypes import ARCHETYPES
from spy_der.synthetic.universe import UniverseCatalog


def _candidate() -> MarketCandidateView:
    return MarketCandidateView(
        candidate_id="c1",
        family="long_call",
        direction="bullish",
        maximum_loss=Decimal("1"),
        capital_required=Decimal("1"),
        geometry_hash="sha256:c1",
        expiration=date(2026, 7, 22),
        utility=1.0,
    )


def _packet(snap_id: str, *, pnl: float) -> MarketPacket:
    return MarketPacket(
        schema_version=MARKET_PACKET_SCHEMA,
        snapshot_id=snap_id,
        session_date=date(2026, 7, 22),
        symbol="SPY",
        underlying_price=Decimal("600"),
        data_quality=1.0,
        forecast_uncertainty=0.1,
        candidates=(_candidate(),),
        forecast={
            "labels": {
                "realized_pnl": pnl,
                "true_direction": "bullish",
                "realized_pnl_by_candidate": {"c1": pnl},
            }
        },
        generated_at=datetime(2026, 7, 22, 15, 0, tzinfo=UTC),
    )


def _outcome(snap_id: str, *, pnl: float) -> OutcomePacket:
    return OutcomePacket(
        schema_version=OUTCOME_PACKET_SCHEMA,
        snapshot_id=snap_id,
        session_date=date(2026, 7, 22),
        symbol="SPY",
        candidate_id="c1",
        action="TRADE",
        realized_pnl=Decimal(str(pnl)),
        settled=True,
        labels={
            "true_direction": "bullish",
            "realized_pnl_by_candidate": {"c1": str(pnl)},
        },
    )


class _ArchetypeAwareUniverse:
    """Provider that loses on crash and wins elsewhere — plus coverage."""

    def generate(self, specification: Any):
        for packet, _outcome, _cov in self._world(specification):
            yield packet

    def generate_result(self, specification: Any) -> Any:
        packets: list[MarketPacket] = []
        outcomes: list[OutcomePacket] = []
        coverage: dict[str, dict[str, int]] = {}
        for packet, outcome, cov in self._world(specification):
            packets.append(packet)
            outcomes.append(outcome)
            for arch, regimes in cov.items():
                bucket = coverage.setdefault(arch, {})
                for regime, minutes in regimes.items():
                    bucket[regime] = bucket.get(regime, 0) + minutes

        class _Result:
            pass

        result = _Result()
        result.packets = packets
        result.outcomes = outcomes
        result.coverage = coverage
        return result

    def _world(self, specification: Any):
        arch = getattr(specification, "archetype", None) or getattr(
            specification, "start_archetype", "calm_pin"
        )
        pnl = -2.0 if arch == "crash" else 1.0
        uid = getattr(specification, "universe_id", "u")
        for i in range(2):
            snap = f"{uid}-{i}"
            yield (
                _packet(snap, pnl=pnl),
                _outcome(snap, pnl=pnl),
                {arch: {"pin": 30, "breakout": 10}},
            )


def test_save_and_load_curriculum_weights(tmp_path: Path) -> None:
    weights = dict.fromkeys(ARCHETYPES, 1.0)
    weights["crash"] = 3.5
    path = save_curriculum_weights(
        tmp_path,
        weights=weights,
        generation=2,
        weak_archetypes=["crash"],
    )
    assert path is not None
    assert path.name == CURRICULUM_WEIGHTS_FILENAME
    loaded = load_curriculum_weights(tmp_path)
    assert loaded is not None
    assert loaded["crash"] == 3.5
    assert loaded["calm_pin"] == 1.0


def test_load_curriculum_weights_missing_file(tmp_path: Path) -> None:
    assert load_curriculum_weights(tmp_path) is None
    assert load_curriculum_weights(None) is None


def _champion() -> ActiveDecisionAuthority:
    return ActiveDecisionAuthority(
        agent=MockDecisionAgent(
            action=AgentEntryAction.SELECT_CANDIDATE,
            candidate_id="c1",
            size_scalar=1.0,
        ),
        authority_name="champion",
    )


def test_universe_phase_persists_and_reloads_weights(tmp_path: Path) -> None:
    # Full lattice guarantees every archetype (including crash) is scored once.
    cfg = DojoConfig(
        configs_dir=str(tmp_path),
        universes_per_gen=6,
        generations=1,
        universe_days=1,
        full_lattice=True,
        skip_recorded=True,
        skip_learner=True,
        force_universe=True,
    )
    authority = _champion()
    out = run_universe_phase(
        cfg,
        _ArchetypeAwareUniverse(),
        authorities={"champion": authority},
        evaluator=OutcomeCandidateEvaluator(),
    )
    assert out["status"] == "ok"
    assert "remediation" in out
    assert out["remediation"]["headline"]
    assert (tmp_path / CURRICULUM_WEIGHTS_FILENAME).is_file()

    evolution = out["evolution"]
    assert evolution["weights"]["crash"] > evolution["weights"]["calm_pin"]
    weak_names = [w["archetype"] for w in out["remediation"]["weak_archetypes"]]
    assert "crash" in weak_names
    focus = out["remediation"]["focus"]
    assert focus
    assert all("reasons" in row and row["reasons"] for row in focus)
    # Focus ranking follows evolution weights, not merely negative P&L.
    assert focus[0]["weight"] == max(row["weight"] for row in focus)

    # Second run must seed from the persisted plan and actually sample with it.
    cfg2 = DojoConfig(
        configs_dir=str(tmp_path),
        universes_per_gen=8,
        generations=1,
        universe_days=1,
        force_universe=True,
    )
    out2 = run_universe_phase(
        cfg2,
        _ArchetypeAwareUniverse(),
        authorities={"champion": authority},
        evaluator=OutcomeCandidateEvaluator(),
    )
    assert out2["seeded_from_prior_weights"] is True
    assert out2["prior_influenced_sampling"] is True
    assert out2["remediation"]["prior_influenced_sampling"] is True


def test_full_lattice_blends_prior_even_when_gen0_ignores_sampling(
    tmp_path: Path,
) -> None:
    """Weekly gen 0 is exhaustive, but prior curriculum still shapes the plan."""
    prior = {a: 1.0 for a in ARCHETYPES}
    prior["crash"] = 4.0
    save_curriculum_weights(tmp_path, weights=prior, generation=3, weak_archetypes=["crash"])

    cfg = DojoConfig(
        configs_dir=str(tmp_path),
        universes_per_gen=6,
        generations=1,  # measurement only — no remediation sample this run
        universe_days=1,
        full_lattice=True,
        force_universe=True,
    )
    out = run_universe_phase(
        cfg,
        _ArchetypeAwareUniverse(),
        authorities={"champion": _champion()},
        evaluator=OutcomeCandidateEvaluator(),
    )
    assert out["prior_influenced_sampling"] is False
    assert out["prior_blended_into_plan"] is True
    assert out["evolution"]["blended_from_prior"] is True
    # Inertia must lift crash above the fresh proposed weight.
    assert (
        out["evolution"]["weights"]["crash"]
        > out["evolution"]["proposed_weights"]["crash"]
    )
    assert "inertia" in (out["remediation"].get("prior_note") or "").lower()


def test_multi_generation_evolves_catalog_weights(tmp_path: Path) -> None:
    cfg = DojoConfig(
        configs_dir=str(tmp_path),
        universes_per_gen=12,
        generations=2,
        universe_days=1,
        full_lattice=False,
        force_universe=True,
        catalog_seed=99,
    )
    out = run_universe_phase(
        cfg,
        _ArchetypeAwareUniverse(),
        authorities={"champion": _champion()},
        evaluator=OutcomeCandidateEvaluator(),
    )
    assert len(out["generation_plans"]) == 2
    first = out["generation_plans"][0]["weights"]
    # After gen 0, crash should already be upweighted for gen 1 sampling.
    assert first["crash"] > first["calm_pin"]
    # Crash should also appear more often once weights apply.
    matrix = out["archetype_matrix"]
    assert matrix.get("crash", {}).get("n_universes", 0) >= 1


def test_full_lattice_second_gen_is_weighted_sample(tmp_path: Path) -> None:
    """Gen 0 measures the lattice; gen 1+ remediate via sample, not another lattice."""
    catalog = UniverseCatalog(seed=1, days=1, generation=0)
    lattice_size = catalog.lattice_size
    cfg = DojoConfig(
        configs_dir=str(tmp_path),
        universes_per_gen=6,
        generations=2,
        universe_days=1,
        full_lattice=True,
        force_universe=True,
    )
    out = run_universe_phase(
        cfg,
        _ArchetypeAwareUniverse(),
        authorities={"champion": _champion()},
        evaluator=OutcomeCandidateEvaluator(),
    )
    # lattice + sample(6), not 2 * lattice
    assert out["n_universes"] == lattice_size + 6
    assert out["evolution"]["weights"]["crash"] > out["evolution"]["weights"]["calm_pin"]
    # Gen 0 → gen 1 plan exists so remediation sampling was driven by evolution.
    assert out["generation_plans"][0]["weights"]["crash"] > out["generation_plans"][0][
        "weights"
    ]["calm_pin"]
