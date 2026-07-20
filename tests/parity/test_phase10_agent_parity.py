"""Phase 10 agent-framework parity (master spec §37-§45, §65)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from spy_der.agents import (
    DeterministicDecisionAgent,
    FailClosedAgentRuntime,
    MockDecisionAgent,
    build_agent_decision_packet,
    compare_agents,
)
from spy_der.contracts import (
    AgentEntryAction,
    Candidate,
    CandidateLeg,
    CandidateUniverse,
    CanonicalMarketSnapshot,
    DebitCredit,
    OptionType,
    PolicyAction,
    PolicyDecisionView,
    SessionStatus,
    to_canonical_json,
)

_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = _ROOT / "baseline" / "expected_outputs" / "phase10" / "agent_decision.json"


def _candidate(cid: str) -> Candidate:
    exp = date(2026, 1, 5)
    return Candidate(
        candidate_id=cid,
        snapshot_id="snap-p10-parity",
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


def test_agent_decision_parity() -> None:
    exp = date(2026, 1, 5)
    snap = CanonicalMarketSnapshot(
        snapshot_id="snap-p10-parity",
        content_hash="sha256:p10",
        timestamp=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        session_date=exp,
        underlying_symbol="SPY",
        underlying_price=Decimal("100"),
        session_status=SessionStatus.OPEN,
        minutes_to_close=90,
    )
    universe = CandidateUniverse(
        universe_id="u",
        snapshot_id="snap-p10-parity",
        candidates=(_candidate("c1"), _candidate("c2")),
    )
    now = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    packet = build_agent_decision_packet(
        snapshot=snap,
        universe=universe,
        created_at=now,
        policy_views=(
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
        ),
        risk_max_size_scalar=0.8,
    )
    det = DeterministicDecisionAgent()
    resp = FailClosedAgentRuntime(det).decide_entry(packet, now=now)
    cmp = compare_agents(
        packet,
        authoritative=det,
        shadows={"mock": MockDecisionAgent(action=AgentEntryAction.NO_EDGE)},
    )
    payload = {
        "packet_id": packet.packet_id,
        "packet_hash": packet.packet_hash,
        "candidate_ids": [c.candidate_id for c in packet.candidates],
        "authoritative": {
            "action": resp.action.value,
            "candidate_id": resp.candidate_id,
            "size_scalar": resp.size_scalar,
            "reason_codes": list(resp.reason_codes),
        },
        "shadow": {
            "action_disagreement": cmp.action_disagreement,
            "candidate_disagreement": cmp.candidate_disagreement,
        },
    }
    actual = json.loads(to_canonical_json(payload))
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert actual == expected
