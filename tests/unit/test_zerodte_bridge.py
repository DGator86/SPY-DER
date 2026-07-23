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
from spy_der.integrations.zerodte.provider import reset_shadow_tick_cache
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


_BLEEDING_PUT_CREDIT_RECORD = {
    "n_trades": 12,
    "win_rate": 0.33,
    "total_pnl": -180.0,
    "ev_bias_per_share": -0.42,
    "by_family": [
        {"family": "put_credit", "n_trades": 8, "total_pnl": -220.0, "win_rate": 0.25},
        {"family": "call_credit", "n_trades": 4, "total_pnl": 40.0, "win_rate": 0.75},
    ],
    "lessons": ["family=put_credit is bleeding: -$220.00 over 8 trades"],
}


def test_track_record_reaches_packet_and_prompt() -> None:
    from spy_der.agents.prompts import build_entry_prompt
    from spy_der.contracts.agents import AgentDecisionPacket

    captured: list[AgentDecisionPacket] = []

    class _Capture(DeterministicDecisionAgent):
        def decide_entry(self, packet):  # type: ignore[override]
            captured.append(packet)
            return super().decide_entry(packet)

    reset_shadow_tick_cache()
    decide_shadow_tick(
        snapshot_id="snap-tr",
        symbol="SPY",
        session_date=date(2026, 7, 20),
        underlying_price=Decimal("600"),
        candidates=_cands(),
        now=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
        agent=_Capture(),
        track_record=_BLEEDING_PUT_CREDIT_RECORD,
    )
    assert captured, "agent never saw a packet"
    record = captured[0].track_record
    assert record is not None
    assert record.n_trades == 12
    assert record.by_family[0].family == "put_credit"
    prompt = build_entry_prompt(captured[0])
    assert "track_record" in prompt["user"]
    assert "put_credit is bleeding" in prompt["user"]


def test_track_record_derates_bleeding_family() -> None:
    # Without feedback the deterministic agent picks c1 (put_credit, higher
    # utility). With a losing put_credit record, c2 (call_credit) outranks it.
    reset_shadow_tick_cache()
    baseline = decide_shadow_tick(
        snapshot_id="snap-a",
        symbol="SPY",
        session_date=date(2026, 7, 20),
        underlying_price=Decimal("600"),
        candidates=_cands(),
        now=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
        agent=DeterministicDecisionAgent(),
    )
    assert baseline.candidate_id == "c1"
    informed = decide_shadow_tick(
        snapshot_id="snap-b",
        symbol="SPY",
        session_date=date(2026, 7, 20),
        underlying_price=Decimal("600"),
        candidates=_cands(),
        now=datetime(2026, 7, 20, 15, 1, tzinfo=UTC),
        agent=DeterministicDecisionAgent(),
        track_record=_BLEEDING_PUT_CREDIT_RECORD,
    )
    # A changed record must invalidate the unchanged-candidates cache AND
    # change the selection — this is the learning loop acting.
    assert informed.candidate_id == "c2"


def test_track_record_derate_when_every_family_bleeds() -> None:
    reset_shadow_tick_cache()
    record = {
        "n_trades": 16,
        "win_rate": 0.2,
        "total_pnl": -300.0,
        "by_family": [
            {"family": "put_credit", "n_trades": 8, "total_pnl": -200.0, "win_rate": 0.2},
            {"family": "call_credit", "n_trades": 8, "total_pnl": -100.0, "win_rate": 0.2},
        ],
    }
    decision = decide_shadow_tick(
        snapshot_id="snap-c",
        symbol="SPY",
        session_date=date(2026, 7, 20),
        underlying_price=Decimal("600"),
        candidates=_cands(),
        now=datetime(2026, 7, 20, 15, 2, tzinfo=UTC),
        agent=DeterministicDecisionAgent(),
        track_record=record,
    )
    assert decision.action == "TRADE"
    assert decision.size_scalar == 0.5
    assert "track_record_derate" in decision.reason_codes


def test_malformed_track_record_degrades_to_no_feedback() -> None:
    reset_shadow_tick_cache()
    decision = decide_shadow_tick(
        snapshot_id="snap-d",
        symbol="SPY",
        session_date=date(2026, 7, 20),
        underlying_price=Decimal("600"),
        candidates=_cands(),
        now=datetime(2026, 7, 20, 15, 3, tzinfo=UTC),
        agent=DeterministicDecisionAgent(),
        track_record={"n_trades": "garbage", "by_family": "nope"},
    )
    assert decision.action == "TRADE"
    assert decision.candidate_id == "c1"


def test_decide_shadow_tick_fails_closed_on_bad_input() -> None:
    # fill_probability out of [0, 1] would raise during packet build; the
    # bridge must fail closed to ABSTAIN instead of propagating.
    bad = (
        ShadowCandidateView(
            candidate_id="c1",
            family="put_credit",
            direction="bearish",
            maximum_loss=Decimal("4"),
            capital_required=Decimal("4"),
            geometry_hash="sha256:c1",
            expiration=date(2026, 7, 20),
            fill_probability=1.5,
        ),
    )
    decision = decide_shadow_tick(
        snapshot_id="snap-bad",
        symbol="SPY",
        session_date=date(2026, 7, 20),
        underlying_price=600,
        candidates=bad,
        now=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
        agent=DeterministicDecisionAgent(),
    )
    assert decision.action == "ABSTAIN"
    assert decision.reason_codes == ("spy_der_bridge_error",)


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
