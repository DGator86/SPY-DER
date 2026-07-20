"""AI decision authority — entry, exit, track, analyze."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from spy_der.agents import (
    AiDecisionAuthority,
    DeterministicDecisionAgent,
    FailClosedAgentRuntime,
    GrokDecisionAgent,
    MockDecisionAgent,
    build_agent_decision_packet,
    build_position_decision_packet,
    build_position_prompt,
    default_agent_registry,
    parse_position_json,
)
from spy_der.contracts import (
    AgentEntryAction,
    AgentPositionAction,
    Candidate,
    CandidateLeg,
    CandidateUniverse,
    CanonicalMarketSnapshot,
    DebitCredit,
    OptionType,
    PolicyAction,
    PolicyDecisionView,
    SessionStatus,
)
from spy_der.contracts.agents import ExitPolicySummary, SnapshotSummary
from spy_der.contracts.events import JournalEventType
from spy_der.contracts.positions import PositionState, PositionStatus
from spy_der.runtime import ShadowAiLoop


def _candidate(cid: str = "c1") -> Candidate:
    exp = date(2026, 1, 5)
    return Candidate(
        candidate_id=cid,
        snapshot_id="snap-ai",
        family="long_call",
        direction="bullish",
        expiration=exp,
        legs=(
            CandidateLeg(
                option_type=OptionType.CALL,
                strike=Decimal("100"),
                quantity=1,
                expiration=exp,
                contract_id=cid,
            ),
        ),
        entry_type=DebitCredit.DEBIT,
        maximum_profit=None,
        maximum_loss=Decimal("10"),
        breakevens=(),
        capital_required=Decimal("10"),
        terminal_payoff_hash="sha256:pay",
        geometry_hash=f"sha256:{cid}",
    )


def _snapshot() -> CanonicalMarketSnapshot:
    return CanonicalMarketSnapshot(
        snapshot_id="snap-ai",
        content_hash="sha256:ai",
        timestamp=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        session_date=date(2026, 1, 5),
        underlying_symbol="SPY",
        underlying_price=Decimal("100"),
        session_status=SessionStatus.OPEN,
        minutes_to_close=90,
    )


def _entry_packet(**kwargs: object):
    now = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    universe = CandidateUniverse(
        universe_id="u-ai",
        snapshot_id="snap-ai",
        candidates=(_candidate("c1"), _candidate("c2")),
    )
    policy_views = (
        PolicyDecisionView(
            policy_name="ensemble",
            policy_version="v1",
            action=PolicyAction.SELECT_CANDIDATE,
            candidate_id="c1",
            size_cap=0.5,
            confidence=0.7,
            uncertainty=0.3,
            reason_codes=("ensemble_select",),
        ),
    )
    base = dict(
        snapshot=_snapshot(),
        universe=universe,
        created_at=now,
        policy_views=policy_views,
        risk_max_size_scalar=0.8,
        approved_exit_policies=(
            ExitPolicySummary("target_and_stop", "target_and_stop"),
        ),
    )
    base.update(kwargs)
    return build_agent_decision_packet(**base)  # type: ignore[arg-type]


def _open_position(*, contracts: int = 2) -> PositionState:
    return PositionState(
        position_id="pos-ai-1",
        account_id="system_b_grok",
        candidate_id="c1",
        status=PositionStatus.OPEN,
        opened_contracts=contracts,
        open_contracts=contracts,
        entry_price=Decimal("1.00"),
        mark_price=Decimal("1.00"),
        max_loss=Decimal("10"),
        exit_policy_id="target_and_stop",
        opened_at=datetime(2026, 1, 5, 14, 30, tzinfo=UTC),
        geometry_hash="sha256:c1",
    )


def test_authority_entry_tracks_and_journals() -> None:
    packet = _entry_packet()
    authority = AiDecisionAuthority(DeterministicDecisionAgent())
    result = authority.decide_entry(packet, now=packet.created_at)
    assert result.selected
    assert authority.tracker.entry_selects == 1
    types = [e.event_type for e in authority.journal.iter_events()]
    assert JournalEventType.AGENT_REQUESTED in types
    assert JournalEventType.AGENT_DECIDED in types
    analysis = authority.analyze(now=packet.created_at)
    assert "AI[deterministic" in analysis.summary
    assert analysis.levers["entry"]


def test_ai_is_exit_maker_with_hard_floor_override() -> None:
    now = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    pos = _open_position()
    snap = SnapshotSummary(
        snapshot_id="snap-ai",
        symbol="SPY",
        session_date=date(2026, 1, 5),
        underlying_price=Decimal("100"),
    )
    # Mock wants HOLD, but stop floor forces CLOSE via authority.
    agent = MockDecisionAgent(position_action=AgentPositionAction.HOLD)
    authority = AiDecisionAuthority(agent)
    packet = build_position_decision_packet(
        position=pos,
        snapshot=snap,
        mark_price=Decimal("0.60"),
        now=now,
        deterministic_exit_signal="stop",
    )
    result = authority.decide_position(packet, now=now)
    assert result.should_close
    assert result.response.action is AgentPositionAction.CLOSE
    assert "deterministic_exit_floor" in result.response.reason_codes


def test_ai_discretionary_close_without_hard_floor() -> None:
    now = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    pos = _open_position()
    snap = SnapshotSummary(
        snapshot_id="snap-ai",
        symbol="SPY",
        session_date=date(2026, 1, 5),
        underlying_price=Decimal("100"),
    )
    agent = MockDecisionAgent(position_action=AgentPositionAction.CLOSE)
    authority = AiDecisionAuthority(agent)
    packet = build_position_decision_packet(
        position=pos,
        snapshot=snap,
        mark_price=Decimal("1.10"),
        now=now,
        deterministic_exit_signal="",
    )
    result = authority.decide_position(packet, now=now)
    assert result.should_close
    assert authority.tracker.position_closes == 1


def test_position_parser_and_prompt() -> None:
    now = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    pos = _open_position()
    snap = SnapshotSummary(
        snapshot_id="snap-ai",
        symbol="SPY",
        session_date=date(2026, 1, 5),
        underlying_price=Decimal("100"),
    )
    packet = build_position_decision_packet(
        position=pos, snapshot=snap, mark_price=Decimal("1.05"), now=now
    )
    prompt = build_position_prompt(packet)
    assert "HOLD" in prompt["system"]
    assert "api_key" not in prompt["user"].lower()
    raw = json.dumps(
        {
            "action": "REDUCE",
            "reduce_fraction": 0.5,
            "confidence": 0.6,
            "uncertainty": 0.4,
            "reason_codes": ["trim"],
            "rationale": "take some off",
        }
    )
    parsed = parse_position_json(raw, packet)
    assert parsed.action is AgentPositionAction.REDUCE
    assert parsed.reduce_fraction == 0.5


def test_grok_position_via_transport() -> None:
    now = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    pos = _open_position()
    snap = SnapshotSummary(
        snapshot_id="snap-ai",
        symbol="SPY",
        session_date=date(2026, 1, 5),
        underlying_price=Decimal("100"),
    )
    packet = build_position_decision_packet(
        position=pos, snapshot=snap, mark_price=Decimal("1.20"), now=now
    )
    raw = json.dumps(
        {
            "action": "CLOSE",
            "reduce_fraction": 0,
            "confidence": 0.8,
            "uncertainty": 0.2,
            "reason_codes": ["ai_exit"],
            "rationale": "edge gone",
        }
    )

    def transport(_url: str, _headers: dict[str, str], _body: dict) -> str:
        return raw

    agent = GrokDecisionAgent(transport=transport, api_key="test-key")
    assert agent.capabilities.supports_position_decisions
    runtime = FailClosedAgentRuntime(agent)
    resp = runtime.decide_position(packet, now=now)
    assert resp.action is AgentPositionAction.CLOSE


def test_shadow_ai_loop_entry_then_exit() -> None:
    packet = _entry_packet()
    agent = MockDecisionAgent(
        action=AgentEntryAction.SELECT_CANDIDATE,
        candidate_id="c1",
        size_scalar=1.0,
        position_action=AgentPositionAction.CLOSE,
    )
    loop = ShadowAiLoop.with_agent(agent)
    t1 = loop.tick(packet=packet, now=packet.created_at)
    assert t1.entry_action == "SELECT_CANDIDATE"
    assert t1.opened_position_ids
    assert loop.live_state()["role"] == "ai_decision_maker"
    assert loop.live_state()["open_positions"]

    pos_id = t1.opened_position_ids[0]
    later = packet.created_at + timedelta(minutes=5)
    t2 = loop.tick(
        packet=packet,
        marks={pos_id: Decimal("1.25")},
        now=later,
        allow_entries=False,
    )
    assert t2.closed_position_ids
    assert loop.authority.tracker.position_closes >= 1


def test_default_registry_includes_grok() -> None:
    reg = default_agent_registry()
    assert "grok" in reg.providers()
    agent = reg.create("grok")
    assert agent.identity.provider == "grok"
