"""Phase 10 agent framework tests (spec §37-§45)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from spy_der.agents import (
    AgentRegistry,
    DeterministicDecisionAgent,
    FailClosedAgentRuntime,
    GrokDecisionAgent,
    MockDecisionAgent,
    RecordedDecisionAgent,
    build_agent_decision_packet,
    build_entry_prompt,
    compare_agents,
    parse_agent_json,
)
from spy_der.agents.security import assert_no_secrets
from spy_der.agents.validation import ValidationError, validate_agent_response
from spy_der.contracts import (
    AgentDecisionResponse,
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
)


def _candidate(cid: str = "c1") -> Candidate:
    exp = date(2026, 1, 5)
    return Candidate(
        candidate_id=cid,
        snapshot_id="snap-p10",
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
        snapshot_id="snap-p10",
        content_hash="sha256:p10",
        timestamp=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        session_date=date(2026, 1, 5),
        underlying_symbol="SPY",
        underlying_price=Decimal("100"),
        session_status=SessionStatus.OPEN,
        minutes_to_close=90,
    )


def _packet(**kwargs: object):
    now = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    universe = CandidateUniverse(
        universe_id="u1",
        snapshot_id="snap-p10",
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
    )
    base.update(kwargs)
    return build_agent_decision_packet(**base)  # type: ignore[arg-type]


def test_packet_has_no_secret_keys() -> None:
    packet = _packet()
    prompt = build_entry_prompt(packet)
    assert "api_key" not in prompt["user"].lower()
    with pytest.raises(ValueError, match="forbidden"):
        assert_no_secrets({"authorization": "secret"})


def test_deterministic_agent_selects_policy_candidate() -> None:
    packet = _packet()
    runtime = FailClosedAgentRuntime(DeterministicDecisionAgent())
    resp = runtime.decide_entry(packet, now=packet.created_at)
    assert resp.action is AgentEntryAction.SELECT_CANDIDATE
    assert resp.candidate_id == "c1"
    assert resp.size_scalar <= packet.risk_max_size_scalar


def test_cannot_select_outside_whitelist() -> None:
    packet = _packet()
    bad = AgentDecisionResponse(
        packet_id=packet.packet_id,
        packet_hash=packet.packet_hash,
        action=AgentEntryAction.SELECT_CANDIDATE,
        candidate_id="not-in-packet",
        size_scalar=0.1,
    )
    with pytest.raises(ValidationError, match="whitelist"):
        validate_agent_response(packet, bad, now=packet.created_at)
    runtime = FailClosedAgentRuntime(
        MockDecisionAgent(action=AgentEntryAction.SELECT_CANDIDATE, candidate_id="nope")
    )
    resp = runtime.decide_entry(packet, now=packet.created_at)
    assert resp.action is AgentEntryAction.ABSTAIN


def test_size_cannot_exceed_cap() -> None:
    packet = _packet(risk_max_size_scalar=0.2)
    bad = AgentDecisionResponse(
        packet_id=packet.packet_id,
        packet_hash=packet.packet_hash,
        action=AgentEntryAction.SELECT_CANDIDATE,
        candidate_id="c1",
        size_scalar=0.9,
        geometry_hash="sha256:c1",
    )
    with pytest.raises(ValidationError, match="risk cap"):
        validate_agent_response(packet, bad, now=packet.created_at)


def test_hard_veto_blocks_selection() -> None:
    packet = _packet(hard_vetoes=("HALT",))
    runtime = FailClosedAgentRuntime(DeterministicDecisionAgent())
    resp = runtime.decide_entry(packet, now=packet.created_at)
    assert resp.action is AgentEntryAction.ABSTAIN


def test_expired_packet_abstains() -> None:
    packet = _packet()
    runtime = FailClosedAgentRuntime(DeterministicDecisionAgent())
    later = packet.expires_at + timedelta(seconds=1)
    resp = runtime.decide_entry(packet, now=later)
    assert resp.action is AgentEntryAction.ABSTAIN
    assert "validation_failure" in resp.reason_codes


def test_parser_and_grok_transport() -> None:
    packet = _packet()
    raw = json.dumps(
        {
            "action": "SELECT_CANDIDATE",
            "candidate_id": "c2",
            "size_scalar": 0.25,
            "confidence": 0.6,
            "uncertainty": 0.4,
            "reason_codes": ["model_pick"],
            "rationale": "prefer c2",
        }
    )
    parsed = parse_agent_json(raw, packet, model_id="grok-2", prompt_version="p")
    assert parsed.candidate_id == "c2"

    def transport(_url: str, headers: dict[str, str], _body: dict) -> str:
        # Ensure adapter can send auth header without leaking into packet/prompt.
        assert "Authorization" in headers
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": raw,
                        }
                    }
                ]
            }
        )

    agent = GrokDecisionAgent(transport=transport, api_key="test-key-not-for-prod")
    runtime = FailClosedAgentRuntime(agent)
    resp = runtime.decide_entry(packet, now=packet.created_at)
    assert resp.action is AgentEntryAction.SELECT_CANDIDATE
    assert resp.candidate_id == "c2"


def test_recorded_and_registry_and_shadow() -> None:
    packet = _packet()
    det = DeterministicDecisionAgent()
    auth = FailClosedAgentRuntime(det).decide_entry(packet, now=packet.created_at)
    recorded = RecordedDecisionAgent()
    recorded.record(packet.packet_hash, auth)
    replay = FailClosedAgentRuntime(recorded).decide_entry(packet, now=packet.created_at)
    assert replay.action == auth.action
    assert replay.candidate_id == auth.candidate_id

    registry = AgentRegistry()
    registry.register("deterministic", DeterministicDecisionAgent)
    registry.register("mock", lambda: MockDecisionAgent(action=AgentEntryAction.NO_EDGE))
    assert "deterministic" in registry.providers()

    cmp = compare_agents(
        packet,
        authoritative=det,
        shadows={"mock": MockDecisionAgent(action=AgentEntryAction.NO_EDGE)},
    )
    assert cmp.action_disagreement
    # Shadow comparison must not mutate packet/authority semantics.
    assert cmp.authoritative.action is AgentEntryAction.SELECT_CANDIDATE
