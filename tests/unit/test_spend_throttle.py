"""Grok spend throttles: predictor interval cache + reviewer conviction gate."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from spy_der.contracts import AgentEntryAction
from spy_der.contracts.agents import AgentDecisionResponse
from spy_der.integrations.zerodte import predict_shadow_tick
from spy_der.integrations.zerodte.prediction import _reset_forecast_cache
from spy_der.integrations.zerodte.provider import _review_gate_passes


def _market(**kw: float) -> SimpleNamespace:
    base: dict[str, float] = dict(
        spot=602.0, vwap=601.0, call_wall=606.0, put_wall=596.0, gamma_flip=598.0,
        net_gex=3e9, gex_pct_rank=0.8, rsi=58.0, adx=20.0, cvd_slope=0.03,
        expected_range=3.0, straddle_breakeven=4.0, vix=13.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _CountingAgent:
    """Grok-shaped agent that counts how many times it is actually called."""

    def __init__(self) -> None:
        self.calls = 0
        self.identity = SimpleNamespace(model_id="grok-4.5")

    def call_raw(self, prompt: dict[str, str]) -> str:
        self.calls += 1
        return json.dumps(
            {
                "bias": "bullish", "target": 603.0, "target_low": 601.0,
                "target_high": 605.0, "confidence": 0.7, "rationale": "x",
            }
        )


def setup_function() -> None:
    _reset_forecast_cache()


# --- predictor interval throttle ------------------------------------------- #
def test_predictor_reuses_cached_forecast_within_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_PREDICT_INTERVAL_SEC", "3600")
    agent = _CountingAgent()
    p1 = predict_shadow_tick(market=_market(), agent=agent)
    p2 = predict_shadow_tick(market=_market(spot=603.5), agent=agent)
    assert agent.calls == 1  # second tick served from cache, no HTTP
    assert p1 is not None and p2 is not None
    assert p1.source == "grok" and p2.source == "grok"
    assert p2.target == p1.target  # Grok's forecast reused
    assert p2.spot_at_pred == pytest.approx(603.5)  # re-anchored to live spot


def test_predictor_interval_zero_calls_every_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_PREDICT_INTERVAL_SEC", "0")
    agent = _CountingAgent()
    predict_shadow_tick(market=_market(), agent=agent)
    predict_shadow_tick(market=_market(), agent=agent)
    assert agent.calls == 2


# --- reviewer conviction / size gate --------------------------------------- #
def _resp(*, confidence: float = 0.5, size: float = 0.5) -> AgentDecisionResponse:
    return AgentDecisionResponse(
        packet_id="p",
        packet_hash="h",
        action=AgentEntryAction.SELECT_CANDIDATE,
        candidate_id="c1",
        size_scalar=size,
        confidence=confidence,
    )


def test_review_gate_reviews_all_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XAI_REVIEW_MIN_CONFIDENCE", raising=False)
    monkeypatch.delenv("XAI_REVIEW_MIN_SIZE", raising=False)
    assert _review_gate_passes(_resp(confidence=0.05, size=0.05)) is True


def test_review_gate_skips_low_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_REVIEW_MIN_CONFIDENCE", "0.6")
    assert _review_gate_passes(_resp(confidence=0.4)) is False
    assert _review_gate_passes(_resp(confidence=0.7)) is True


def test_review_gate_skips_small_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_REVIEW_MIN_SIZE", "0.5")
    assert _review_gate_passes(_resp(size=0.2)) is False
    assert _review_gate_passes(_resp(size=0.6)) is True


def test_review_gate_ignores_malformed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_REVIEW_MIN_CONFIDENCE", "not-a-number")
    assert _review_gate_passes(_resp(confidence=0.1)) is True
