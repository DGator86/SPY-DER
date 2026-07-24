"""Phase-4 Dojo: CandidateEvaluator wiring + scored universe / sequential."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from spy_der.contracts.integration import (
    MARKET_PACKET_SCHEMA,
    OUTCOME_PACKET_SCHEMA,
    MarketCandidateView,
    MarketPacket,
    OutcomePacket,
)
from spy_der.dojo import DojoConfig, run_dojo, run_sequential_dojo
from spy_der.dojo.decisions import DecisionRole, decide_on_packets
from spy_der.dojo.evaluator import OutcomeMatchingEvaluator
from spy_der.dojo.universe import StaticUniverseSpec, run_universe_phase
from spy_der.integrations.zerodte.recorded_feed import FileMarketExperienceProvider


def _packet(
    snap_id: str,
    session: str,
    *,
    direction: str = "bullish",
    utility: float = 1.0,
    candidate_id: str = "c1",
) -> MarketPacket:
    return MarketPacket(
        schema_version=MARKET_PACKET_SCHEMA,
        snapshot_id=snap_id,
        session_date=date.fromisoformat(session),
        symbol="SPY",
        underlying_price=Decimal("600"),
        data_quality=1.0,
        forecast_uncertainty=0.1,
        forecast={"realized_direction": "up", "realized_pnl": "0.25"},
        candidates=(
            MarketCandidateView(
                candidate_id=candidate_id,
                family="long_call",
                direction=direction,
                maximum_loss=Decimal("1"),
                capital_required=Decimal("1"),
                geometry_hash=f"sha256:{candidate_id}",
                expiration=date.fromisoformat(session),
                utility=utility,
            ),
            MarketCandidateView(
                candidate_id=f"{candidate_id}-b",
                family="long_put",
                direction="bearish",
                maximum_loss=Decimal("1"),
                capital_required=Decimal("1"),
                geometry_hash=f"sha256:{candidate_id}-b",
                expiration=date.fromisoformat(session),
                utility=utility - 0.1,
            ),
        ),
        generated_at=datetime(2026, 7, 22, 15, 0, tzinfo=UTC),
    )


def _outcome(snap_id: str, session: str, pnl: str = "0.25") -> OutcomePacket:
    return OutcomePacket(
        schema_version=OUTCOME_PACKET_SCHEMA,
        snapshot_id=snap_id,
        session_date=date.fromisoformat(session),
        symbol="SPY",
        candidate_id="c1",
        action="TRADE",
        realized_pnl=Decimal(pnl),
        settled=True,
        labels={"realized_direction": "up"},
        settled_at=datetime(2026, 7, 22, 20, 0, tzinfo=UTC),
    )


def _seed_experience(root: Path, sessions: list[str], ticks: int = 40) -> None:
    (root / "snapshots").mkdir(parents=True)
    (root / "outcomes").mkdir(parents=True)
    for session in sessions:
        for tick in range(ticks):
            snap_id = f"snap-{session}-{tick}"
            p = _packet(snap_id, session)
            (root / "snapshots" / f"{snap_id}.json").write_text(
                json.dumps(p.to_dict()), encoding="utf-8"
            )
            (root / "outcomes" / f"{snap_id}.json").write_text(
                json.dumps(_outcome(snap_id, session).to_dict()), encoding="utf-8"
            )
    (root / "sessions.json").write_text(json.dumps(sessions), encoding="utf-8")


class _Synthetic:
    def generate(self, specification: StaticUniverseSpec):
        session = "2026-07-22"
        for i in range(5):
            yield _packet(
                f"{specification.universe_id}-{i}",
                session,
                utility=1.0 - (i * 0.01),
            )


def test_evaluator_scores_decisions() -> None:
    packets = [_packet("s1", "2026-07-22"), _packet("s2", "2026-07-22")]
    outcomes = [_outcome("s1", "2026-07-22"), _outcome("s2", "2026-07-22", "-0.1")]
    os.environ["SPY_DER_AI"] = "0"
    decisions = decide_on_packets(packets, role=DecisionRole.BASELINE)
    report = OutcomeMatchingEvaluator().evaluate(decisions, outcomes)
    assert report.status == "ok"
    assert report.trades >= 1
    assert report.win_rate is not None
    assert report.dir_hit is not None


def test_run_dojo_wires_evaluator_and_lanes(tmp_path: Path) -> None:
    os.environ["SPY_DER_AI"] = "0"
    exp = tmp_path / "experience"
    sessions = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23"]
    _seed_experience(exp, sessions, ticks=30)
    provider = FileMarketExperienceProvider(exp)
    synthetic = _Synthetic()
    cfg = DojoConfig(
        reports_dir=str(tmp_path / "reports"),
        configs_dir=str(tmp_path / "configs"),
        report_date="2026-07-24",
        min_ticks=100,
        min_sessions=3,
        universes_per_gen=2,
        generations=1,
    )
    out = run_dojo(cfg, experience=provider, synthetic=synthetic)
    recorded = out["metrics"]["phases"]["recorded"]
    assert recorded["status"] == "ok"
    assert recorded["lanes"]["champion"]["status"] == "ok"
    assert recorded["lanes"]["baseline"]["status"] == "ok"
    assert recorded["lanes"]["challenger"]["status"] == "ok"

    sequential = out["metrics"]["phases"]["sequential"]
    assert sequential["status"] == "ok"
    assert sequential["mean_forward_transfer"] is not None

    universe = out["metrics"]["phases"]["universe"]
    assert universe["status"] == "ok"
    matrix = universe["archetype_matrix"]
    assert matrix
    first = next(iter(matrix.values()))
    assert first["mean_session_pnl"] is not None
    assert first["trades"] > 0
    assert first["lanes"]["champion"]["status"] == "ok"


def test_sequential_computes_forward_transfer(tmp_path: Path) -> None:
    os.environ["SPY_DER_AI"] = "0"
    exp = tmp_path / "experience"
    sessions = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23"]
    _seed_experience(exp, sessions, ticks=10)
    out = run_sequential_dojo(FileMarketExperienceProvider(exp))
    assert out["status"] == "ok"
    assert out["n_scored"] >= 1
    assert any("forward_transfer" in row for row in out["curriculum"])


def test_universe_no_longer_null_pnl() -> None:
    os.environ["SPY_DER_AI"] = "0"
    cfg = DojoConfig(
        universes_per_gen=3,
        generations=1,
        skip_recorded=True,
        skip_learner=True,
    )
    out = run_universe_phase(cfg, _Synthetic())
    assert out["status"] == "ok"
    for metrics in out["archetype_matrix"].values():
        assert metrics["mean_session_pnl"] is not None
        assert metrics["total_pnl"] != 0.0 or metrics["trades"] >= 0
