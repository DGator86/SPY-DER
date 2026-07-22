"""Cost controls for the live Grok path (reasoning effort, killswitch, cache)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from spy_der.agents.grok import GrokConfig, GrokDecisionAgent
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
        utility=0.35 - i * 0.01,
        v3_rank=i + 1,
    )


def test_non_reasoning_trader_omits_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPY_DER_AI_CACHE", "0")
    monkeypatch.setenv("XAI_REVIEW_ENABLED", "0")
    reset_shadow_tick_cache()
    captured: dict[str, Any] = {}

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
        captured.update(body)
        return (
            '{"action":"NO_EDGE","candidate_id":null,"size_scalar":0,'
            '"confidence":0.1,"uncertainty":0.5,"reason_codes":["t"],'
            '"rationale":"t","exit_policy_id":null}'
        )

    agent = GrokDecisionAgent(
        transport=transport, api_key="k", cfg=GrokConfig(auto_http=False)
    )
    decide_shadow_tick(
        snapshot_id="s1",
        symbol="SPY",
        session_date=date(2026, 7, 22),
        underlying_price=600,
        candidates=(_cand(),),
        now=datetime(2026, 7, 22, 15, 0, tzinfo=UTC),
        agent=agent,
    )
    assert "reasoning_effort" not in captured
    assert captured["model"] == "grok-4.20-0309-non-reasoning"
    assert captured["max_completion_tokens"] == 512


def test_reasoning_effort_and_max_tokens_env_for_reasoning_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPY_DER_AI_CACHE", "0")
    monkeypatch.setenv("XAI_REVIEW_ENABLED", "0")
    monkeypatch.setenv("XAI_MODEL", "grok-4.5")
    monkeypatch.setenv("XAI_REASONING_EFFORT", "medium")
    monkeypatch.setenv("XAI_MAX_COMPLETION_TOKENS", "256")
    reset_shadow_tick_cache()
    captured: dict[str, Any] = {}

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
        captured.update(body)
        return (
            '{"action":"NO_EDGE","candidate_id":null,"size_scalar":0,'
            '"confidence":0.1,"uncertainty":0.5,"reason_codes":["t"],'
            '"rationale":"t","exit_policy_id":null}'
        )

    agent = GrokDecisionAgent(transport=transport, api_key="k")
    decide_shadow_tick(
        snapshot_id="s2",
        symbol="SPY",
        session_date=date(2026, 7, 22),
        underlying_price=600,
        candidates=(_cand(),),
        now=datetime(2026, 7, 22, 15, 1, tzinfo=UTC),
        agent=agent,
    )
    assert captured["reasoning_effort"] == "medium"
    assert captured["max_completion_tokens"] == 256


def test_killswitch_disables_grok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-test-key-not-used")
    monkeypatch.setenv("SPY_DER_AI", "0")
    monkeypatch.setenv("XAI_REVIEW_ENABLED", "0")
    reset_shadow_tick_cache()
    decision = decide_shadow_tick(
        snapshot_id="s3",
        symbol="SPY",
        session_date=date(2026, 7, 22),
        underlying_price=600,
        candidates=(_cand(),),
        now=datetime(2026, 7, 22, 15, 2, tzinfo=UTC),
    )
    assert decision.provider == "deterministic"


def test_empty_candidates_skip_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_REVIEW_ENABLED", "0")
    calls = {"n": 0}

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
        calls["n"] += 1
        return '{"action":"NO_EDGE","reason_codes":["t"],"rationale":"t"}'

    agent = GrokDecisionAgent(transport=transport, api_key="k")
    reset_shadow_tick_cache()
    decision = decide_shadow_tick(
        snapshot_id="s4",
        symbol="SPY",
        session_date=date(2026, 7, 22),
        underlying_price=600,
        candidates=(),
        now=datetime(2026, 7, 22, 15, 3, tzinfo=UTC),
        agent=agent,
    )
    assert decision.action == "NO_EDGE"
    assert "no_candidates" in decision.reason_codes
    assert calls["n"] == 0


def test_cache_skips_repeat_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPY_DER_AI_CACHE", "1")
    monkeypatch.setenv("XAI_REVIEW_ENABLED", "0")
    calls = {"n": 0}

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
        calls["n"] += 1
        return (
            '{"action":"NO_EDGE","candidate_id":null,"size_scalar":0,'
            '"confidence":0.2,"uncertainty":0.4,"reason_codes":["t"],'
            '"rationale":"cached","exit_policy_id":null}'
        )

    agent = GrokDecisionAgent(transport=transport, api_key="k")
    reset_shadow_tick_cache()
    kwargs = dict(
        symbol="SPY",
        session_date=date(2026, 7, 22),
        underlying_price=600,
        candidates=(_cand(1), _cand(2)),
        agent=agent,
    )
    d1 = decide_shadow_tick(
        snapshot_id="snap-a",
        now=datetime(2026, 7, 22, 15, 4, tzinfo=UTC),
        **kwargs,
    )
    d2 = decide_shadow_tick(
        snapshot_id="snap-b",  # different snapshot, same candidates
        now=datetime(2026, 7, 22, 15, 5, tzinfo=UTC),
        **kwargs,
    )
    assert calls["n"] == 1
    assert d1.rationale == d2.rationale == "cached"


def test_top_k_limits_candidates_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPY_DER_AI_TOP_K", "2")
    monkeypatch.setenv("SPY_DER_AI_CACHE", "0")
    monkeypatch.setenv("XAI_REVIEW_ENABLED", "0")
    seen: list[int] = []

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
        import json

        user = json.loads(body["messages"][1]["content"])
        seen.append(len(user["candidates"]))
        return (
            '{"action":"NO_EDGE","candidate_id":null,"size_scalar":0,'
            '"confidence":0.1,"uncertainty":0.5,"reason_codes":["t"],'
            '"rationale":"t","exit_policy_id":null}'
        )

    agent = GrokDecisionAgent(transport=transport, api_key="k")
    reset_shadow_tick_cache()
    decide_shadow_tick(
        snapshot_id="s5",
        symbol="SPY",
        session_date=date(2026, 7, 22),
        underlying_price=600,
        candidates=tuple(_cand(i) for i in range(10)),
        now=datetime(2026, 7, 22, 15, 6, tzinfo=UTC),
        agent=agent,
    )
    assert seen == [2]
