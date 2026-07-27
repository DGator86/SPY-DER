"""SPY-DER <-> 0DTE bridge provider tests."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from spy_der.agents import DeterministicDecisionAgent, MockDecisionAgent
from spy_der.contracts import AgentEntryAction
from spy_der.decisions.shadow import reset_shadow_tick_cache
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


# --------------------------------------------------------------------------- #
# Market and forecast context on the live shadow path                         #
# --------------------------------------------------------------------------- #
_MARKET = {
    "net_gex_bn": -1.8,
    "gamma_sign": -1,
    "gamma_flip": "598.50",
    "call_wall": "605",
    "put_wall": "595",
    "flip_cushion": 0.0025,
    "expected_move": "3.10",
    "expected_move_consumed": 0.4,
    "vix": 16.2,
    "vix3m": 18.0,
    "pcr_volume": 1.32,
    "sector_align": 0.36,
    "technicals": {"1m.rsi": 38.2, "15m.adx": 27.4, "1h.dist_to_vwap": -0.31},
    "data_quality_flags": ["spot:tradier_quote"],
}
_FORECAST = {"forecast_id": "fc-9", "p_up_30m": 0.42, "expected_return_30m": -0.0011}


def _capture_packet(**kw):  # type: ignore[no-untyped-def]
    from spy_der.contracts.agents import AgentDecisionPacket

    captured: list[AgentDecisionPacket] = []

    class _Capture(DeterministicDecisionAgent):
        def decide_entry(self, packet):  # type: ignore[override]
            captured.append(packet)
            return super().decide_entry(packet)

    reset_shadow_tick_cache()
    decide_shadow_tick(
        snapshot_id="snap-mc",
        symbol="SPY",
        session_date=date(2026, 7, 20),
        underlying_price=Decimal("600"),
        candidates=_cands(),
        now=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
        agent=_Capture(),
        **kw,
    )
    assert captured, "agent never saw a packet"
    return captured[0]


def test_market_context_reaches_the_packet_and_prompt() -> None:
    """The gap the bridge always could have closed: the agent can see the market."""
    from spy_der.agents.prompts import build_entry_prompt

    packet = _capture_packet(market_context=_MARKET, forecast=_FORECAST)
    context = packet.market_context
    assert context is not None
    assert context.underlying_price == Decimal("600")
    assert context.net_gex_bn == -1.8
    assert context.gamma_sign == -1
    assert context.call_wall == Decimal("605")
    assert context.vix == 16.2
    assert dict(context.technicals)["1m.rsi"] == 38.2
    assert context.data_quality_flags == ("spot:tradier_quote",)

    body = json.loads(build_entry_prompt(packet)["user"])
    assert body["market_context"]["net_gex_bn"] == -1.8
    assert body["market_context"]["technicals"]["15m.adx"] == 27.4


def test_the_bridge_forecast_is_no_longer_discarded() -> None:
    packet = _capture_packet(market_context=_MARKET, forecast=_FORECAST)
    assert packet.forecast_context is not None
    assert dict(packet.forecast_context.horizons)["p_up_30m"] == 0.42
    assert packet.forecast_context.forecast_id == "fc-9"


def test_a_malformed_market_context_degrades_per_field() -> None:
    """A bad VIX must not cost the agent its view of the walls."""
    packet = _capture_packet(
        market_context={
            "vix": "not-a-number",
            "net_gex_bn": float("nan"),
            "call_wall": "605",
            "technicals": {"1m.rsi": "bad", "15m.adx": 27.4},
        }
    )
    context = packet.market_context
    assert context is not None
    assert context.vix is None
    assert context.net_gex_bn is None  # NaN is dropped, not stored
    assert context.call_wall == Decimal("605")
    assert dict(context.technicals) == {"15m.adx": 27.4}


def test_no_market_context_still_carries_the_underlying() -> None:
    packet = _capture_packet()
    assert packet.market_context is not None
    assert packet.market_context.underlying_price == Decimal("600")
    assert packet.forecast_context is None


def test_unknown_context_keys_are_dropped_not_passed_through() -> None:
    """The packet must not grow untyped fields from an upstream caller."""
    packet = _capture_packet(market_context={"totally_unknown": 1.0, "vix": 16.0})
    assert packet.market_context is not None
    assert packet.market_context.vix == 16.0
    assert not hasattr(packet.market_context, "totally_unknown")


def test_a_regime_change_invalidates_the_unchanged_candidates_cache() -> None:
    """The same geometry deserves a different answer once the market has moved."""
    first = _capture_packet(market_context={**_MARKET, "gamma_sign": -1})
    second = _capture_packet(market_context={**_MARKET, "gamma_sign": 1})
    assert first.packet_hash != second.packet_hash


def test_tick_noise_does_not_bust_the_cache() -> None:
    """Keying on the raw technical vector would make every tick a paid call."""
    reset_shadow_tick_cache()
    common = dict(
        snapshot_id="snap-cache",
        symbol="SPY",
        session_date=date(2026, 7, 20),
        underlying_price=Decimal("600"),
        candidates=_cands(),
        now=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
    )
    calls: list[int] = []

    class _Counting(DeterministicDecisionAgent):
        def decide_entry(self, packet):  # type: ignore[override]
            calls.append(1)
            return super().decide_entry(packet)

    decide_shadow_tick(
        agent=_Counting(), market_context={**_MARKET, "vix": 16.2}, **common
    )
    decide_shadow_tick(
        # A 0.1 vol point and a different RSI: noise, not a regime change.
        agent=_Counting(),
        market_context={**_MARKET, "vix": 16.3, "technicals": {"1m.rsi": 39.0}},
        **common,
    )
    assert len(calls) == 1
