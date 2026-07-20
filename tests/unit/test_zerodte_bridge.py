"""SPY-DER <-> 0DTE bridge provider tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from spy_der.agents import DeterministicDecisionAgent, MockDecisionAgent
from spy_der.contracts import AgentEntryAction
from spy_der.integrations.zerodte import (
    PARALLEL_TRACK_ID,
    ShadowCandidateView,
    decide_shadow_tick,
    parallel_track_payload,
)
from spy_der.runtime import write_live_state_file
from spy_der.runtime.runner import RunnerConfig, SpyDerVpsRunner


def _cands() -> tuple[ShadowCandidateView, ...]:
    return (
        ShadowCandidateView(
            candidate_id="c1",
            family="put_credit",
            direction="bearish",
            maximum_loss=Decimal("4"),
            capital_required=Decimal("4"),
            geometry_hash="sha256:c1",
            expiration=date(2026, 7, 20),
            utility=0.2,
            v3_rank=1,
        ),
        ShadowCandidateView(
            candidate_id="c2",
            family="call_credit",
            direction="bullish",
            maximum_loss=Decimal("4"),
            capital_required=Decimal("4"),
            geometry_hash="sha256:c2",
            expiration=date(2026, 7, 20),
            utility=0.1,
            v3_rank=2,
        ),
    )


def test_decide_shadow_tick_selects_candidate() -> None:
    agent = MockDecisionAgent(
        action=AgentEntryAction.SELECT_CANDIDATE,
        candidate_id="c2",
        size_scalar=0.5,
    )
    decision = decide_shadow_tick(
        snapshot_id="snap-1",
        symbol="SPY",
        session_date=date(2026, 7, 20),
        underlying_price=Decimal("600"),
        candidates=_cands(),
        now=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
        agent=agent,
    )
    assert decision.action == "TRADE"
    assert decision.candidate_id == "c2"
    assert decision.track == PARALLEL_TRACK_ID
    payload = parallel_track_payload(decision)
    assert payload["label"] == "SPY-DER"
    assert payload["action"] == "TRADE"


def test_deterministic_fallback_no_edge_on_empty() -> None:
    decision = decide_shadow_tick(
        snapshot_id="snap-empty",
        symbol="SPY",
        session_date=date(2026, 7, 20),
        underlying_price=100,
        candidates=(),
        now=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
        agent=DeterministicDecisionAgent(),
    )
    assert decision.action in {"NO_EDGE", "ABSTAIN"}


def test_state_writer_atomic(tmp_path: Path) -> None:
    path = tmp_path / "spy_der_state.json"
    write_live_state_file(path, {"track": "spy_der", "ok": True})
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "spy_der" in text


def test_runner_heartbeat_payload(tmp_path: Path) -> None:
    cfg = RunnerConfig(live_state_path=str(tmp_path / "state.json"), interval_seconds=0.01)
    runner = SpyDerVpsRunner(config=cfg)
    payload = runner._heartbeat_payload(
        {"phase": "active", "primary": "system_b"}
    )
    assert payload["track"] == PARALLEL_TRACK_ID
    assert payload["live_execution_enabled"] is False
    assert payload["parallel"]["label"] == "SPY-DER"
