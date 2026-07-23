"""SPY-DER 0DTE price-prediction tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from spy_der.integrations.zerodte import (
    PREDICTION_SCHEMA,
    ShadowMarketView,
    SpyDerPrediction,
    predict_shadow_tick,
)
from spy_der.integrations.zerodte.prediction import _forecast_agent, _reset_forecast_cache


def setup_function() -> None:
    # Isolate the predictor throttle cache between tests.
    _reset_forecast_cache()


def _market(**kw: float) -> SimpleNamespace:
    base: dict[str, float] = dict(
        spot=602.0, vwap=601.0, call_wall=606.0, put_wall=596.0, gamma_flip=598.0,
        net_gex=3e9, gex_pct_rank=0.8, rsi=58.0, adx=20.0, cvd_slope=0.03,
        expected_range=3.0, straddle_breakeven=4.0, vix=13.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_deterministic_without_key_produces_ordered_cone() -> None:
    p = predict_shadow_tick(market=_market(), now_iso="2026-07-22T11:00:00-04:00")
    assert isinstance(p, SpyDerPrediction)
    assert p.source == "deterministic"
    assert p.target_low <= p.target <= p.target_high
    assert 0.0 <= p.confidence <= 1.0
    assert p.generated_at == "2026-07-22T11:00:00-04:00"
    assert p.schema == PREDICTION_SCHEMA
    labels = {lv.label for lv in p.key_levels}
    assert {"Call wall", "Put wall", "γ-flip", "VWAP"} <= labels  # noqa: RUF001


def test_as_dict_is_json_serialisable_and_flattens_levels() -> None:
    p = predict_shadow_tick(market=_market())
    assert p is not None
    d = p.as_dict()
    json.dumps(d)  # must not raise
    assert isinstance(d["key_levels"], list)
    assert d["key_levels"][0]["label"] == "Call wall"
    assert isinstance(d["drivers"], list)


def test_bullish_and_bearish_direction() -> None:
    up = predict_shadow_tick(
        market=_market(spot=603.5, vwap=601.0, gamma_flip=598.0, rsi=66.0, cvd_slope=0.06))
    down = predict_shadow_tick(
        market=_market(spot=597.0, vwap=600.0, gamma_flip=599.0, rsi=36.0, cvd_slope=-0.06))
    assert up is not None and down is not None
    assert up.bias == "bullish" and up.target >= up.spot_at_pred
    assert down.bias == "bearish" and down.target <= down.spot_at_pred


def test_target_capped_by_walls() -> None:
    p = predict_shadow_tick(
        market=_market(spot=605.6, call_wall=606.0, rsi=92.0, cvd_slope=1.0, expected_range=8.0))
    assert p is not None
    assert p.target <= 606.0 + 1e-6


def test_returns_none_without_spot() -> None:
    assert predict_shadow_tick(market=SimpleNamespace(spot=None)) is None
    assert ShadowMarketView.from_market(SimpleNamespace(spot=0.0)) is None


@dataclass
class _StubIdentity:
    model_id: str = "grok-4.5"


class _StubAgent:
    """Minimal Grok-shaped agent returning a canned JSON forecast."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.identity = _StubIdentity()

    def call_raw(self, prompt: dict[str, str]) -> str:
        assert "system" in prompt and "user" in prompt
        return "here is my forecast: " + json.dumps(self._payload)


def test_grok_agent_path_is_used_and_labelled() -> None:
    agent = _StubAgent({
        "bias": "bullish", "target": 603.2, "target_low": 601.0,
        "target_high": 605.0, "confidence": 0.7, "rationale": "pin above VWAP",
    })
    p = predict_shadow_tick(market=_market(), agent=agent)
    assert p is not None
    assert p.source == "grok"
    assert p.model_id == "grok-4.5"
    assert p.bias == "bullish"
    assert p.rationale == "pin above VWAP"
    assert p.target_low <= p.target <= p.target_high


def test_grok_output_is_clamped_to_walls_and_bounds() -> None:
    # Grok returns a wild target and an out-of-range confidence; both are clamped.
    agent = _StubAgent({
        "bias": "bullish", "target": 999.0, "target_low": 990.0,
        "target_high": 1000.0, "confidence": 5.0, "rationale": "moon",
    })
    p = predict_shadow_tick(market=_market(call_wall=606.0), agent=agent)
    assert p is not None
    assert p.target <= 606.0 + 1e-6
    assert 0.0 <= p.confidence <= 1.0
    assert p.target_low <= p.target <= p.target_high


def test_killswitch_disables_grok_forecast(monkeypatch: pytest.MonkeyPatch) -> None:
    # The predictor honours the system-wide killswitch (SPY_DER_AI / XAI_ENABLED),
    # not a bespoke env var — so a global AI-off fully disables Grok forecasts.
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    monkeypatch.setenv("SPY_DER_AI", "0")
    assert _forecast_agent() is None
    monkeypatch.setenv("SPY_DER_AI", "1")
    monkeypatch.setenv("XAI_ENABLED", "0")
    assert _forecast_agent() is None


def test_grok_parse_failure_falls_back_to_deterministic() -> None:
    class _BadAgent:
        identity = _StubIdentity()

        def call_raw(self, prompt: dict[str, str]) -> str:
            return "not json at all"

    p = predict_shadow_tick(market=_market(), agent=_BadAgent())
    assert p is not None and p.source == "deterministic"
