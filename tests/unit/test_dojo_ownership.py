"""Dojo ownership-boundary tests — no 0DTE internal imports."""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from spy_der.contracts.integration import (
    MARKET_PACKET_SCHEMA,
    OUTCOME_PACKET_SCHEMA,
    MarketCandidateView,
    MarketPacket,
    OutcomePacket,
)
from spy_der.dojo import DojoConfig, run_dojo, run_sequential_dojo
from spy_der.dojo.curriculum import DojoCadence, plan_for_cadence
from spy_der.dojo.evaluation import forgetting_penalty, forward_transfer
from spy_der.integrations.zerodte.recorded_feed import FileMarketExperienceProvider
from spy_der.learning import run_learning_cycle
from spy_der.learning.promotion import PromotionError, promote_pending

FORBIDDEN_0DTE_IMPORTS = {
    "backtest",
    "journal",
    "matrix_universe",
    "walk_forward",
    "adaptive_learning",
    "chain_store",
    "decision_engine",
}


def test_dojo_package_does_not_import_zerodte_internals() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "spy_der" / "dojo"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in FORBIDDEN_0DTE_IMPORTS, path
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                assert top not in FORBIDDEN_0DTE_IMPORTS, path


def test_run_dojo_without_providers(tmp_path: Path) -> None:
    cfg = DojoConfig(
        reports_dir=str(tmp_path / "reports"),
        configs_dir=str(tmp_path / "configs"),
        report_date="2026-07-24",
    )
    out = run_dojo(cfg)
    assert out["report_date"] == "2026-07-24"
    assert (tmp_path / "reports" / "latest.json").is_file()
    assert any(f["flag"] == "no_recorded_tape" for f in out["flags"])


def test_run_dojo_with_file_experience(tmp_path: Path) -> None:
    exp = tmp_path / "experience"
    (exp / "snapshots").mkdir(parents=True)
    (exp / "outcomes").mkdir(parents=True)
    packet = MarketPacket(
        schema_version=MARKET_PACKET_SCHEMA,
        snapshot_id="snap-1",
        session_date=date(2026, 7, 22),
        symbol="SPY",
        underlying_price=Decimal("600"),
        data_quality=1.0,
        forecast_uncertainty=0.1,
        candidates=(
            MarketCandidateView(
                candidate_id="c1",
                family="long_call",
                direction="bullish",
                maximum_loss=Decimal("1"),
                capital_required=Decimal("1"),
                geometry_hash="sha256:c1",
                expiration=date(2026, 7, 22),
            ),
        ),
        generated_at=datetime(2026, 7, 22, 15, 0, tzinfo=UTC),
    )
    (exp / "snapshots" / "snap-1.json").write_text(
        __import__("json").dumps(packet.to_dict()), encoding="utf-8"
    )
    # Seed enough sessions for min_sessions via sessions.json
    sessions = ["2026-07-20", "2026-07-21", "2026-07-22"]
    # Duplicate snapshots across session dates for min_ticks
    for session in sessions:
        for tick in range(40):
            snap_id = f"snap-{session}-{tick}"
            p = MarketPacket(
                schema_version=MARKET_PACKET_SCHEMA,
                snapshot_id=snap_id,
                session_date=date.fromisoformat(session),
                symbol="SPY",
                underlying_price=Decimal("600"),
                data_quality=1.0,
                forecast_uncertainty=0.1,
                candidates=packet.candidates,
                generated_at=datetime(2026, 7, 22, 15, 0, tzinfo=UTC),
            )
            (exp / "snapshots" / f"{snap_id}.json").write_text(
                __import__("json").dumps(p.to_dict()), encoding="utf-8"
            )
            outcome = OutcomePacket(
                schema_version=OUTCOME_PACKET_SCHEMA,
                snapshot_id=snap_id,
                session_date=date.fromisoformat(session),
                symbol="SPY",
                candidate_id="c1",
                action="TRADE",
                realized_pnl=Decimal("0.1"),
                settled=True,
                settled_at=datetime(2026, 7, 22, 20, 0, tzinfo=UTC),
            )
            (exp / "outcomes" / f"{snap_id}.json").write_text(
                __import__("json").dumps(outcome.to_dict()), encoding="utf-8"
            )
    (exp / "sessions.json").write_text(
        __import__("json").dumps(sessions), encoding="utf-8"
    )

    provider = FileMarketExperienceProvider(exp)
    cfg = DojoConfig(
        reports_dir=str(tmp_path / "reports"),
        configs_dir=str(tmp_path / "configs"),
        report_date="2026-07-24",
        min_ticks=100,
        min_sessions=3,
        skip_universe=True,
    )
    out = run_dojo(cfg, experience=provider)
    assert out["metrics"]["phases"]["recorded"]["status"] in {"ok", "baseline_only"}
    # With enough sessions, learner may stage a pending candidate.
    assert out["metrics"]["phases"]["learner"]["status"] == "ok"
    # Champion must remain untouched — promotion is human-gated.
    assert (tmp_path / "configs" / "champion.json").exists() is False


def test_promotion_requires_human_ack(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    result = run_learning_cycle(
        mode="dojo",
        configs_dir=str(configs),
        experience=None,
        trials=5,
        holdout=0.25,
    )
    assert result["outcome"] in {"no_change", "promotion_recommended"}
    with pytest.raises(PromotionError):
        promote_pending(configs, "missing", human_ack="yes")


def test_sequential_and_curriculum_helpers() -> None:
    assert forward_transfer(1.0, 0.4) == pytest.approx(0.6)
    assert forgetting_penalty([1.0, 1.0], [0.9, 1.0]) == pytest.approx(0.1)
    plan = plan_for_cadence(DojoCadence.DAILY)
    assert plan.recent_days == 3
    assert plan.full_lattice is False
    weekly = plan_for_cadence("weekly")
    assert weekly.full_lattice is True
    out = run_sequential_dojo(None)
    assert out["status"] == "insufficient_data"
