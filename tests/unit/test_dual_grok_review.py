"""Dual-model trader (cheap) + reviewer (flagship on TRADE only)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from spy_der.agents.grok import GrokDecisionAgent
from spy_der.agents.review import apply_trade_review, parse_review_json
from spy_der.contracts.agents import (
    AgentDecisionResponse,
    AgentEntryAction,
)
from spy_der.integrations.zerodte.provider import (
    ShadowCandidateView,
    decide_shadow_tick,
    reset_shadow_tick_cache,
)


def _cand(i: int = 0) -> ShadowCandidateView:
    return ShadowCandidateView(
        candidate_id=f"c{i}",
        family="put_credit_spread",
        direction="bearish",
        maximum_loss=Decimal("120"),
        capital_required=Decimal("120"),
        geometry_hash=f"sha256:{i:064d}",
        expiration=date(2026, 7, 22),
        mid_price=Decimal("0.80"),
        fill_probability=0.9,
        utility=0.4 - i * 0.01,
        v3_rank=i + 1,
    )


def _trade_json(candidate_id: str = "c0") -> str:
    return (
        f'{{"action":"SELECT_CANDIDATE","candidate_id":"{candidate_id}",'
        f'"size_scalar":0.8,"exit_policy_id":"target_and_stop",'
        f'"confidence":0.7,"uncertainty":0.2,'
        f'"reason_codes":["TOP_UTILITY"],"rationale":"trader pick"}}'
    )


def test_reviewer_only_runs_on_trade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPY_DER_AI_CACHE", "0")
    monkeypatch.setenv("XAI_REVIEW_ENABLED", "1")
    monkeypatch.setenv("XAI_REVIEW_MODEL", "grok-4.5")
    models: list[str] = []

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
        models.append(str(body["model"]))
        # First call trader NO_EDGE — reviewer must not run.
        return (
            '{"action":"NO_EDGE","candidate_id":null,"size_scalar":0,'
            '"confidence":0.1,"uncertainty":0.5,"reason_codes":["t"],'
            '"rationale":"flat","exit_policy_id":null}'
        )

    agent = GrokDecisionAgent(transport=transport, api_key="k")
    reset_shadow_tick_cache()
    decide_shadow_tick(
        snapshot_id="s1",
        symbol="SPY",
        session_date=date(2026, 7, 22),
        underlying_price=600,
        candidates=(_cand(),),
        now=datetime(2026, 7, 22, 15, 0, tzinfo=UTC),
        agent=agent,
    )
    assert models == ["grok-4.20-0309-non-reasoning"]


def test_reviewer_approves_trade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPY_DER_AI_CACHE", "0")
    monkeypatch.setenv("XAI_REVIEW_ENABLED", "1")
    monkeypatch.setenv("XAI_REVIEW_MODEL", "grok-4.5")
    monkeypatch.setenv("XAI_REVIEW_REASONING_EFFORT", "low")
    calls: list[dict[str, Any]] = []

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
        calls.append(body)
        if body["model"] == "grok-4.20-0309-non-reasoning":
            return _trade_json("c0")
        assert body["model"] == "grok-4.5"
        assert body.get("reasoning_effort") == "low"
        return (
            '{"action":"APPROVE","size_scalar":null,"reason_codes":["OK"],'
            '"rationale":"looks fine"}'
        )

    agent = GrokDecisionAgent(transport=transport, api_key="k")
    reset_shadow_tick_cache()
    decision = decide_shadow_tick(
        snapshot_id="s2",
        symbol="SPY",
        session_date=date(2026, 7, 22),
        underlying_price=600,
        candidates=(_cand(0), _cand(1)),
        now=datetime(2026, 7, 22, 15, 1, tzinfo=UTC),
        agent=agent,
    )
    assert len(calls) == 2
    assert decision.action == "TRADE"
    assert decision.candidate_id == "c0"
    assert decision.size_scalar == 0.8
    assert decision.trader_model_id == "grok-4.20-0309-non-reasoning"
    assert decision.reviewer_model_id == "grok-4.5"
    assert decision.reviewer_action == "APPROVE"
    assert "reviewer_approve" in decision.reason_codes


def test_reviewer_veto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPY_DER_AI_CACHE", "0")
    monkeypatch.setenv("XAI_REVIEW_ENABLED", "1")
    monkeypatch.setenv("XAI_REVIEW_MODEL", "grok-4.5")

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
        if body["model"].endswith("non-reasoning"):
            return _trade_json("c0")
        return (
            '{"action":"VETO","size_scalar":null,"reason_codes":["RISKY"],'
            '"rationale":"nope"}'
        )

    agent = GrokDecisionAgent(transport=transport, api_key="k")
    reset_shadow_tick_cache()
    decision = decide_shadow_tick(
        snapshot_id="s3",
        symbol="SPY",
        session_date=date(2026, 7, 22),
        underlying_price=600,
        candidates=(_cand(),),
        now=datetime(2026, 7, 22, 15, 2, tzinfo=UTC),
        agent=agent,
    )
    assert decision.action == "NO_EDGE"
    assert decision.reviewer_action == "VETO"
    assert "reviewer_veto" in decision.reason_codes


def test_reviewer_resize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPY_DER_AI_CACHE", "0")
    monkeypatch.setenv("XAI_REVIEW_ENABLED", "1")
    monkeypatch.setenv("XAI_REVIEW_MODEL", "grok-4.5")

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
        if body["model"].endswith("non-reasoning"):
            return _trade_json("c0")
        return (
            '{"action":"RESIZE","size_scalar":0.25,"reason_codes":["CUT"],'
            '"rationale":"smaller"}'
        )

    agent = GrokDecisionAgent(transport=transport, api_key="k")
    reset_shadow_tick_cache()
    decision = decide_shadow_tick(
        snapshot_id="s4",
        symbol="SPY",
        session_date=date(2026, 7, 22),
        underlying_price=600,
        candidates=(_cand(),),
        now=datetime(2026, 7, 22, 15, 3, tzinfo=UTC),
        agent=agent,
        risk_max_size_scalar=1.0,
    )
    assert decision.action == "TRADE"
    assert decision.size_scalar == 0.25
    assert decision.reviewer_action == "RESIZE"
    assert "reviewer_resize" in decision.reason_codes


def test_review_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPY_DER_AI_CACHE", "0")
    monkeypatch.setenv("XAI_REVIEW_ENABLED", "0")
    models: list[str] = []

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
        models.append(str(body["model"]))
        return _trade_json("c0")

    agent = GrokDecisionAgent(transport=transport, api_key="k")
    reset_shadow_tick_cache()
    decision = decide_shadow_tick(
        snapshot_id="s5",
        symbol="SPY",
        session_date=date(2026, 7, 22),
        underlying_price=600,
        candidates=(_cand(),),
        now=datetime(2026, 7, 22, 15, 4, tzinfo=UTC),
        agent=agent,
    )
    assert models == ["grok-4.20-0309-non-reasoning"]
    assert decision.action == "TRADE"
    assert decision.reviewer_action == ""


def test_parse_and_apply_review_helpers() -> None:
    review = parse_review_json(
        '{"action":"APPROVE","size_scalar":0.5,"reason_codes":["A"],"rationale":"ok"}',
        model_id="grok-4.5",
    )
    trader = AgentDecisionResponse(
        packet_id="p",
        packet_hash="h",
        action=AgentEntryAction.SELECT_CANDIDATE,
        candidate_id="c0",
        size_scalar=0.9,
        confidence=0.6,
        uncertainty=0.2,
        reason_codes=("T",),
        rationale="trader",
        model_id="trader-model",
    )
    out = apply_trade_review(trader, review, risk_max_size_scalar=1.0)
    assert out.action is AgentEntryAction.SELECT_CANDIDATE
    assert out.size_scalar == 0.5
    assert out.model_id == "grok-4.5"
