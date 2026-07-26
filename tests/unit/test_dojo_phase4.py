"""Phase-4 Dojo evaluation — CandidateEvaluator + authority wiring."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from spy_der.agents.mock import MockDecisionAgent
from spy_der.contracts.agents import AgentEntryAction
from spy_der.contracts.integration import (
    MARKET_PACKET_SCHEMA,
    OUTCOME_PACKET_SCHEMA,
    MarketCandidateView,
    MarketPacket,
    OutcomePacket,
)
from spy_der.dojo.authority import (
    ActiveDecisionAuthority,
    ChallengerDecisionAuthority,
    DeterministicBaselineAuthority,
    default_authorities,
)
from spy_der.dojo.config import DojoConfig
from spy_der.dojo.evaluation import OutcomeCandidateEvaluator, evaluate_decisions_rich
from spy_der.dojo.outcomes import outcome_from_market_labels
from spy_der.dojo.runner import run_dojo
from spy_der.dojo.sequential import SequentialDojoConfig, run_sequential_dojo
from spy_der.dojo.universe import StaticUniverseSpec, run_universe_phase
from spy_der.integrations.zerodte.recorded_feed import FileMarketExperienceProvider
from spy_der.learning.learner import staging_gates_pass
from spy_der.learning.memories import load_failure_episodes, load_lessons


def _candidate(
    cid: str = "c1", *, direction: str = "bullish", utility: float = 1.0
) -> MarketCandidateView:
    return MarketCandidateView(
        candidate_id=cid,
        family="long_call",
        direction=direction,
        maximum_loss=Decimal("1"),
        capital_required=Decimal("1"),
        geometry_hash=f"sha256:{cid}",
        expiration=date(2026, 7, 22),
        utility=utility,
    )


def _packet(
    snap_id: str,
    session: date,
    *,
    pnl: float | None = 0.1,
    true_direction: str = "bullish",
    candidates: tuple[MarketCandidateView, ...] | None = None,
) -> MarketPacket:
    forecast: dict[str, Any] = {}
    if pnl is not None:
        forecast["labels"] = {
            "realized_pnl": pnl,
            "true_direction": true_direction,
            "realized_pnl_by_candidate": {"c1": pnl, "c2": pnl - 0.05},
        }
    return MarketPacket(
        schema_version=MARKET_PACKET_SCHEMA,
        snapshot_id=snap_id,
        session_date=session,
        symbol="SPY",
        underlying_price=Decimal("600"),
        data_quality=1.0,
        forecast_uncertainty=0.1,
        candidates=candidates or (_candidate(),),
        forecast=forecast,
        generated_at=datetime(2026, 7, 22, 15, 0, tzinfo=UTC),
    )


class _FakeUniverse:
    def generate(self, specification: StaticUniverseSpec):
        session = date(2026, 7, 22)
        for i in range(3):
            yield _packet(
                f"{specification.universe_id}-{i}",
                session,
                pnl=0.2 if specification.archetype != "chop" else -0.1,
                true_direction="bullish",
            )


def _seed_experience(
    root: Path, *, sessions: list[str], ticks: int = 40
) -> FileMarketExperienceProvider:
    (root / "snapshots").mkdir(parents=True)
    (root / "outcomes").mkdir(parents=True)
    for session in sessions:
        for tick in range(ticks):
            snap_id = f"snap-{session}-{tick}"
            packet = _packet(snap_id, date.fromisoformat(session), pnl=0.1)
            (root / "snapshots" / f"{snap_id}.json").write_text(
                __import__("json").dumps(packet.to_dict()), encoding="utf-8"
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
                labels={"true_direction": "bullish"},
                settled_at=datetime(2026, 7, 22, 20, 0, tzinfo=UTC),
            )
            (root / "outcomes" / f"{snap_id}.json").write_text(
                __import__("json").dumps(outcome.to_dict()), encoding="utf-8"
            )
    (root / "sessions.json").write_text(
        __import__("json").dumps(sessions), encoding="utf-8"
    )
    return FileMarketExperienceProvider(root)


def test_target_architecture_doc_gives_spy_der_the_dojo() -> None:
    """Dojo ownership is asserted by the *authoritative* architecture doc.

    `OWNERSHIP_BOUNDARY.md` described the interim split and is superseded; the
    Dojo claim it made is now a subset of SPY-DER owning the whole stack.
    """
    text = Path("docs/TARGET_ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "| Dojo and learning | SPY-DER |" in text
    assert "| 0DTE | Archived |" in text


def test_superseded_ownership_doc_points_at_the_target() -> None:
    """The old doc must not be readable as still-current guidance."""
    text = Path("docs/OWNERSHIP_BOUNDARY.md").read_text(encoding="utf-8")
    assert "SUPERSEDED" in text.splitlines()[0]
    assert "TARGET_ARCHITECTURE.md" in text
    # It must not be usable to argue a capability stays in 0DTE.
    assert "Do not use it to justify leaving a capability in" in text


def test_outcome_from_market_labels() -> None:
    packet = _packet("s1", date(2026, 7, 22), pnl=0.25)
    outcome = outcome_from_market_labels(packet)
    assert outcome is not None
    assert float(outcome.realized_pnl or 0) == pytest.approx(0.25)
    assert outcome.labels.get("true_direction") == "bullish"


def test_rich_evaluation_metrics() -> None:
    from spy_der.dojo.decisions import DojoDecision

    decisions = [
        DojoDecision("a", "TRADE", "c1", confidence=0.8, direction="bullish"),
        DojoDecision("b", "TRADE", "c1", confidence=0.2, direction="bearish"),
    ]
    outcomes = [
        OutcomePacket(
            schema_version=OUTCOME_PACKET_SCHEMA,
            snapshot_id="a",
            session_date=date(2026, 7, 21),
            symbol="SPY",
            candidate_id="c1",
            action="TRADE",
            realized_pnl=Decimal("1"),
            settled=True,
            labels={
                "true_direction": "bullish",
                "realized_pnl_by_candidate": {"c1": "1", "c2": "2"},
            },
        ),
        OutcomePacket(
            schema_version=OUTCOME_PACKET_SCHEMA,
            snapshot_id="b",
            session_date=date(2026, 7, 22),
            symbol="SPY",
            candidate_id="c1",
            action="TRADE",
            realized_pnl=Decimal("-1"),
            settled=True,
            labels={
                "true_direction": "bullish",
                "realized_pnl_by_candidate": {"c1": "-1", "c2": "0"},
            },
        ),
    ]
    report = evaluate_decisions_rich(decisions, outcomes)
    assert report.status == "ok"
    assert report.n_matched == 2
    assert report.dir_hit == pytest.approx(0.5)
    assert report.regret is not None and report.regret > 0
    assert report.calibration_error is not None


def test_universe_scores_with_authorities(tmp_path: Path) -> None:
    cfg = DojoConfig(
        configs_dir=str(tmp_path / "configs"),
        universes_per_gen=3,
        generations=1,
        skip_recorded=True,
        skip_learner=True,
    )
    out = run_universe_phase(
        cfg,
        _FakeUniverse(),
        authorities=default_authorities(),
        evaluator=OutcomeCandidateEvaluator(),
    )
    assert out["status"] == "ok"
    assert out["n_scored_universes"] > 0
    matrix = out["archetype_matrix"]
    assert any(v.get("mean_session_pnl") is not None for v in matrix.values())
    assert "authorities" in out
    assert "remediation" in out


def test_sequential_inline_scoring(tmp_path: Path) -> None:
    provider = _seed_experience(
        tmp_path / "exp",
        sessions=["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23"],
        ticks=5,
    )
    champion = ActiveDecisionAuthority(
        agent=MockDecisionAgent(
            action=AgentEntryAction.SELECT_CANDIDATE,
            candidate_id="c1",
            size_scalar=1.0,
        ),
        authority_name="champion",
    )
    # Baseline abstains → FT should be positive when trades win.
    baseline = ActiveDecisionAuthority(
        agent=MockDecisionAgent(action=AgentEntryAction.ABSTAIN),
        authority_name="baseline",
    )
    out = run_sequential_dojo(
        provider,
        cfg=SequentialDojoConfig(min_warm_sessions=2, retention_panel_size=1),
        authorities={"champion": champion, "baseline": baseline},
    )
    assert out["status"] == "ok"
    assert out["mean_forward_transfer"] is not None
    assert out["mean_forward_transfer"] > 0
    assert "retention" in out


def test_run_dojo_wires_evaluator_and_lessons(tmp_path: Path) -> None:
    provider = _seed_experience(
        tmp_path / "exp",
        sessions=["2026-07-20", "2026-07-21", "2026-07-22"],
        ticks=40,
    )
    # Force a negative recorded path for lesson persistence via mock that trades.
    champion = ActiveDecisionAuthority(
        agent=MockDecisionAgent(
            action=AgentEntryAction.SELECT_CANDIDATE,
            candidate_id="c1",
            size_scalar=1.0,
        )
    )
    # Rewrite outcomes to losses so lessons fire.
    for path in (tmp_path / "exp" / "outcomes").glob("*.json"):
        data = __import__("json").loads(path.read_text(encoding="utf-8"))
        data["realized_pnl"] = "-0.2"
        path.write_text(__import__("json").dumps(data), encoding="utf-8")

    cfg = DojoConfig(
        reports_dir=str(tmp_path / "reports"),
        configs_dir=str(tmp_path / "state" / "configs"),
        report_date="2026-07-24",
        min_ticks=100,
        min_sessions=3,
        skip_universe=True,
    )
    out = run_dojo(
        cfg,
        experience=provider,
        authorities={
            "champion": champion,
            "baseline": DeterministicBaselineAuthority(),
        },
        skip_sequential=False,
    )
    assert "sequential" in out["metrics"]["phases"]
    assert out["metrics"]["phases"]["recorded"]["status"] in {"ok", "baseline_only"}
    # Lessons land under state root (parent of configs).
    lessons = load_lessons(tmp_path / "state")
    assert lessons
    episodes = load_failure_episodes(tmp_path / "state")
    assert episodes


def test_staging_gates_block_on_retention_failure() -> None:
    ok, notes = staging_gates_pass(
        recorded_result={"evaluation": {"trades": 1, "total_pnl": 1.0, "win_rate": 1.0}},
        sequential_result={
            "mean_forward_transfer": 0.1,
            "retention": {"ok": False, "detail": "forgot"},
        },
    )
    assert ok is False
    assert any("forgot" in n for n in notes)


def test_challenger_authority_applies_min_confidence() -> None:
    authority = ChallengerDecisionAuthority(changes={"min_confidence": 0.99})
    packet = _packet("x", date(2026, 7, 22), pnl=0.1)
    decision = authority.decide(packet)
    # Deterministic confidence is 0.5 → challenger abstains.
    assert decision.action == "ABSTAIN"
