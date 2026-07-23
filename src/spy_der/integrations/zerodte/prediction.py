"""SPY-DER price prediction for the 0DTE dashboard chart.

``predict_shadow_tick`` reads the market context 0DTE hands over (spot, GEX
call/put walls, gamma flip, VWAP, expected range, momentum) and returns a
structured, drawable forecast: a directional bias, an end-of-session target, a
confidence cone, key levels, and a rationale. When Grok is available it drives
the forecast; otherwise a deterministic "trader" model produces one so the
chart always has something to draw. Grok output is never trusted blindly — it
is clamped to ordered, wall-bounded, in-range values.

The ``market`` argument is duck-typed (0DTE's ``MarketSnapshot``); we read it
via attribute access so this package keeps no dependency on the 0DTE types.
"""

from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass
from typing import Any

from spy_der.agents.parser import ParseError, extract_json_object

__all__ = [
    "PREDICTION_PROMPT_VERSION",
    "PREDICTION_SCHEMA",
    "KeyLevel",
    "ShadowMarketView",
    "SpyDerPrediction",
    "predict_shadow_tick",
]

PREDICTION_SCHEMA = "spy_der.prediction.v1"
PREDICTION_PROMPT_VERSION = "spy-der-forecast.v1"


def _getf(market: object, name: str) -> float | None:
    value = getattr(market, name, None)
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass(frozen=True, slots=True)
class KeyLevel:
    price: float
    label: str
    kind: str  # support | resistance | pivot


@dataclass(frozen=True, slots=True)
class ShadowMarketView:
    """Immutable snapshot of the trader context, read from 0DTE's market."""

    spot: float
    vwap: float | None = None
    call_wall: float | None = None
    put_wall: float | None = None
    gamma_flip: float | None = None
    net_gex: float | None = None
    gex_pct_rank: float | None = None
    rsi: float | None = None
    adx: float | None = None
    cvd_slope: float | None = None
    expected_range: float | None = None
    straddle_breakeven: float | None = None
    vix: float | None = None

    @classmethod
    def from_market(cls, market: object) -> ShadowMarketView | None:
        spot = _getf(market, "spot")
        if spot is None or spot <= 0:
            return None
        return cls(
            spot=spot,
            vwap=_getf(market, "vwap"),
            call_wall=_getf(market, "call_wall"),
            put_wall=_getf(market, "put_wall"),
            gamma_flip=_getf(market, "gamma_flip"),
            net_gex=_getf(market, "net_gex"),
            gex_pct_rank=_getf(market, "gex_pct_rank"),
            rsi=_getf(market, "rsi"),
            adx=_getf(market, "adx"),
            cvd_slope=_getf(market, "cvd_slope"),
            expected_range=_getf(market, "expected_range"),
            straddle_breakeven=_getf(market, "straddle_breakeven"),
            vix=_getf(market, "vix"),
        )

    def band(self) -> float:
        band = self.expected_range
        if not band or band <= 0:
            if self.straddle_breakeven is not None:
                band = abs(self.straddle_breakeven - self.spot)
        if not band or band <= 0:
            band = self.spot * 0.004
        return min(band, self.spot * 0.05)

    def key_levels(self) -> tuple[KeyLevel, ...]:
        levels: list[KeyLevel] = []
        if self.call_wall is not None:
            levels.append(KeyLevel(round(self.call_wall, 2), "Call wall", "resistance"))
        if self.put_wall is not None:
            levels.append(KeyLevel(round(self.put_wall, 2), "Put wall", "support"))
        if self.gamma_flip is not None:
            levels.append(KeyLevel(round(self.gamma_flip, 2), "γ-flip", "pivot"))  # noqa: RUF001
        if self.vwap is not None:
            levels.append(KeyLevel(round(self.vwap, 2), "VWAP", "pivot"))
        return tuple(levels)


@dataclass(frozen=True, slots=True)
class SpyDerPrediction:
    bias: str
    spot_at_pred: float
    target: float
    target_low: float
    target_high: float
    band: float
    confidence: float
    key_levels: tuple[KeyLevel, ...]
    rationale: str
    source: str
    model_id: str = ""
    horizon: str = "eod"
    drivers: tuple[str, ...] = ()
    generated_at: str = ""
    schema: str = PREDICTION_SCHEMA

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["key_levels"] = [asdict(level) for level in self.key_levels]
        out["drivers"] = list(self.drivers)
        return out


# --------------------------------------------------------------------------- #
# Deterministic trader model (fallback + Grok clamp reference)                 #
# --------------------------------------------------------------------------- #
def _bias_of(score: float) -> str:
    if score > 0.18:
        return "bullish"
    if score < -0.18:
        return "bearish"
    return "neutral"


def _cap_to_walls(view: ShadowMarketView, target: float) -> float:
    if view.call_wall is not None and target > view.call_wall:
        target = view.spot + (view.call_wall - view.spot) * 0.85
    if view.put_wall is not None and target < view.put_wall:
        target = view.spot - (view.spot - view.put_wall) * 0.85
    return target


def _deterministic(view: ShadowMarketView, now_iso: str) -> SpyDerPrediction:
    spot, band = view.spot, view.band()
    weights = (0.9, 0.6, 0.7, 0.5)
    drivers: list[str] = []
    raw = 0.0
    if view.vwap is not None:
        raw += _clip((spot - view.vwap) / band, -1, 1) * 0.9
        drivers.append("vwap")
    if view.gamma_flip is not None:
        raw += _clip((spot - view.gamma_flip) / band, -1, 1) * 0.6
        drivers.append("gamma_flip")
    if view.rsi is not None:
        raw += _clip((view.rsi - 50.0) / 25.0, -1, 1) * 0.7
        drivers.append("rsi")
    if view.cvd_slope is not None:
        raw += _clip(view.cvd_slope * 4.0, -1, 1) * 0.5
        drivers.append("cvd")
    score = _clip(raw / (sum(weights) or 1.0), -1.0, 1.0)

    pinning = 1.0
    if view.net_gex is not None:
        pinning = 0.55 if view.net_gex > 0 else 1.15
    target = _cap_to_walls(view, spot + score * band * 0.6 * pinning)

    conviction = abs(score)
    trend = _clip((view.adx - 12.0) / 25.0, 0.0, 1.0) if view.adx is not None else 0.4
    positioning = _clip(view.gex_pct_rank, 0.0, 1.0) if view.gex_pct_rank is not None else 0.5
    confidence = _clip(0.20 + 0.45 * conviction + 0.20 * trend + 0.15 * positioning, 0.15, 0.9)
    cone = band * (1.30 - 0.55 * confidence)

    bias = _bias_of(score)
    note = []
    if view.vwap is not None:
        note.append("above VWAP" if spot >= view.vwap else "below VWAP")
    if view.gamma_flip is not None:
        note.append("above γ-flip" if spot >= view.gamma_flip else "below γ-flip")  # noqa: RUF001
    if view.net_gex is not None:
        note.append("positive GEX (pinning)" if view.net_gex > 0 else "negative GEX (unstable)")
    if view.rsi is not None:
        note.append(f"RSI {view.rsi:.0f}")
    rationale = (
        f"{bias.capitalize()} into the close: spot {spot:.2f} "
        + (", ".join(note) if note else "limited context")
        + f". Target {target:.2f} (±{cone:.2f}), confidence {confidence:.0%}."
    )
    return SpyDerPrediction(
        bias=bias,
        spot_at_pred=round(spot, 2),
        target=round(target, 2),
        target_low=round(target - cone, 2),
        target_high=round(target + cone, 2),
        band=round(band, 3),
        confidence=round(confidence, 3),
        key_levels=view.key_levels(),
        rationale=rationale,
        source="deterministic",
        drivers=tuple(drivers),
        generated_at=now_iso,
    )


# --------------------------------------------------------------------------- #
# Grok forecaster                                                              #
# --------------------------------------------------------------------------- #
_PRED_SYSTEM = (
    "You are SPY-DER, a professional 0DTE SPY options trader reading the tape. "
    "Given the current market context, forecast where SPY closes today. Weigh "
    "dealer positioning (GEX call/put walls act as magnets and caps; gamma flip "
    "is the stability pivot; positive net GEX pins, negative amplifies), price "
    "vs VWAP, and momentum (RSI, CVD). Reply with STRICT JSON only:\n"
    '{"bias":"bullish|bearish|neutral","target":<price>,"target_low":<price>,'
    '"target_high":<price>,"confidence":<0..1>,"rationale":"<one sentence>"}. '
    "target must sit between target_low and target_high, stay within roughly one "
    "expected-move band of spot, and respect the walls. No prose outside the JSON."
)


def _context_lines(view: ShadowMarketView) -> str:
    def f(x: float | None, nd: int = 2) -> str:
        return "n/a" if x is None else f"{x:.{nd}f}"

    return (
        f"spot={view.spot:.2f} vwap={f(view.vwap)} call_wall={f(view.call_wall, 1)} "
        f"put_wall={f(view.put_wall, 1)} gamma_flip={f(view.gamma_flip, 1)} "
        f"net_gex={f(view.net_gex, 0)} gex_pct_rank={f(view.gex_pct_rank)} "
        f"expected_range={f(view.expected_range)} rsi={f(view.rsi, 0)} "
        f"adx={f(view.adx, 0)} cvd_slope={f(view.cvd_slope, 3)} vix={f(view.vix, 1)}"
    )


def _grok_prediction(view: ShadowMarketView, now_iso: str, agent: Any) -> SpyDerPrediction:
    """Call Grok for a forecast, then clamp it to safe, ordered, in-range values."""
    prompt = {"system": _PRED_SYSTEM, "user": _context_lines(view)}
    text = agent.call_raw(prompt)
    data = extract_json_object(text)

    band = view.band()
    spot = view.spot

    def num(key: str, default: float) -> float:
        raw = data.get(key)
        try:
            out = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default
        return out if math.isfinite(out) else default

    # Target: trust Grok's level but never let it run past ~1.2 bands or the walls.
    target = _cap_to_walls(view, _clip(num("target", spot), spot - 1.2 * band, spot + 1.2 * band))
    confidence = _clip(num("confidence", 0.4), 0.05, 0.95)
    cone = band * (1.30 - 0.55 * confidence)
    low = min(num("target_low", target - cone), target)
    high = max(num("target_high", target + cone), target)

    bias_raw = str(data.get("bias") or "").strip().lower()
    bias = bias_raw if bias_raw in {"bullish", "bearish", "neutral"} \
        else _bias_of((target - spot) / band)
    rationale = str(data.get("rationale") or "").strip()[:400] or "Grok forecast."

    return SpyDerPrediction(
        bias=bias,
        spot_at_pred=round(spot, 2),
        target=round(target, 2),
        target_low=round(low, 2),
        target_high=round(high, 2),
        band=round(band, 3),
        confidence=round(confidence, 3),
        key_levels=view.key_levels(),
        rationale=rationale,
        source="grok",
        model_id=str(getattr(getattr(agent, "identity", None), "model_id", "") or ""),
        drivers=("grok",),
        generated_at=now_iso,
    )


def _ai_enabled() -> bool:
    """System-wide killswitch, matching the entry/position path.

    ``SPY_DER_AI=0`` or ``XAI_ENABLED=0`` disables all Grok calls (no HTTP).
    """
    for name in ("SPY_DER_AI", "XAI_ENABLED"):
        if os.environ.get(name, "").strip().lower() in {"0", "false", "off", "no"}:
            return False
    return True


def _forecast_agent() -> Any | None:
    """Grok agent when the killswitch is on and a key is present, else None."""
    if not _ai_enabled():
        return None
    if not os.environ.get("XAI_API_KEY"):
        return None
    try:
        from spy_der.agents.grok import GrokConfig, GrokDecisionAgent

        agent = GrokDecisionAgent(cfg=GrokConfig(auto_http=True))
    except Exception:
        return None
    return agent if getattr(agent, "transport", None) is not None else None


def predict_shadow_tick(
    *,
    market: object,
    now_iso: str = "",
    decision: object | None = None,
    agent: Any | None = None,
) -> SpyDerPrediction | None:
    """Forecast SPY's session close for the 0DTE SPY-DER chart.

    ``market`` is 0DTE's market snapshot (duck-typed). Returns ``None`` only when
    there is no usable spot; otherwise always returns a prediction (Grok when
    available, deterministic trader model as the fail-closed fallback).
    """
    view = ShadowMarketView.from_market(market)
    if view is None:
        return None
    resolved = agent if agent is not None else _forecast_agent()
    if resolved is not None:
        try:
            return _grok_prediction(view, now_iso, resolved)
        except (ParseError, ValueError, TypeError, KeyError, RuntimeError, AttributeError):
            pass  # fail-closed to the deterministic model below
    return _deterministic(view, now_iso)
