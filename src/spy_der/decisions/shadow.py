"""SPY-DER shadow decision path — the AI decision authority.

`decide_shadow_tick` is called once per tick with the candidate set for a
snapshot; SPY-DER AI (Grok by default, Deterministic fallback) selects among
them. Live broker routing stays disabled here — paper/shadow only. Enforcement
of hard limits is *not* this module's job: see
:mod:`spy_der.execution.guard`, which re-checks every decision deterministically
before anything can reach an executor.

This module previously lived at ``spy_der.integrations.zerodte.provider``, which
put SPY-DER's own production decision path inside an integrations package named
after the system being retired. It is SPY-DER-owned code and now lives under
:mod:`spy_der.decisions`. ``spy_der.integrations.zerodte`` re-exports it as a
temporary compatibility surface and is deleted at cutover — see
``docs/TARGET_ARCHITECTURE.md``.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from spy_der.agents.authority import AiDecisionAuthority
from spy_der.agents.deterministic import DeterministicDecisionAgent
from spy_der.agents.grok import DEFAULT_TRADER_MODEL_ID, GrokConfig, GrokDecisionAgent
from spy_der.agents.parser import ParseError
from spy_der.agents.protocols import DecisionAgent
from spy_der.agents.review import (
    apply_trade_review,
    make_default_reviewer,
    run_trade_review,
)
from spy_der.agents.security import assert_no_secrets
from spy_der.contracts.agents import (
    AgentCandidateView,
    AgentDecisionPacket,
    AgentDecisionResponse,
    AgentEntryAction,
    DeploymentContext,
    ExitPolicySummary,
    FamilyRecord,
    ForecastContext,
    MarketContext,
    SnapshotSummary,
    TrackRecordSummary,
    make_packet_id,
    packet_hash,
)
from spy_der.contracts.positions import ApprovedExitPolicyId
from spy_der.contracts.serialization import to_canonical_json

__all__ = [
    "PARALLEL_TRACK_ID",
    "PARALLEL_TRACK_LABEL",
    "ShadowCandidateView",
    "SpyDerShadowDecision",
    "decide_shadow_tick",
    "parallel_track_payload",
    "reset_shadow_tick_cache",
]

PARALLEL_TRACK_ID = "spy_der"
PARALLEL_TRACK_LABEL = "SPY-DER"

# Last (content fingerprint -> decision) so unchanged candidate sets skip a paid
# call even when 0DTE rotates snapshot_id every tick.
_LAST_CACHE_KEY: str | None = None
_LAST_DECISION: SpyDerShadowDecision | None = None


def reset_shadow_tick_cache() -> None:
    """Test helper — clear the unpaid-repeat cache."""
    global _LAST_CACHE_KEY, _LAST_DECISION
    _LAST_CACHE_KEY = None
    _LAST_DECISION = None


def _coerce_track_record(raw: Any) -> TrackRecordSummary | None:
    """Build the TrackRecordSummary contract from a caller-supplied plain dict.

    The 0DTE bridge stays decoupled from contract classes by passing a dict;
    anything malformed degrades to None (no feedback) rather than failing the
    tick — the feedback loop must never be the reason a decision errors.
    """
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        n_trades = int(raw.get("n_trades") or 0)
        if n_trades <= 0:
            return None
        families = []
        for f in raw.get("by_family") or []:
            if not isinstance(f, dict) or not f.get("family"):
                continue
            families.append(
                FamilyRecord(
                    family=str(f["family"]),
                    n_trades=int(f.get("n_trades") or 0),
                    total_pnl=Decimal(str(f.get("total_pnl") or 0)),
                    win_rate=float(f.get("win_rate") or 0.0),
                )
            )
        ev_bias = raw.get("ev_bias_per_share")
        return TrackRecordSummary(
            n_trades=n_trades,
            win_rate=float(raw.get("win_rate") or 0.0),
            total_pnl=Decimal(str(raw.get("total_pnl") or 0)),
            ev_bias_per_share=(
                Decimal(str(ev_bias)) if ev_bias is not None else None
            ),
            by_family=tuple(families),
            lessons=tuple(
                str(text) for text in (raw.get("lessons") or []) if text
            ),
        )
    except (ValueError, TypeError, ArithmeticError):
        return None


#: Market-context fields accepted from the bridge, and the type each coerces to.
#: Anything not listed is ignored rather than passed through, so the packet
#: cannot grow untyped fields from an upstream caller.
_MARKET_DECIMAL_FIELDS = (
    "underlying_bid",
    "underlying_ask",
    "gamma_flip",
    "call_wall",
    "put_wall",
    "atm_straddle",
    "expected_move",
)
_MARKET_FLOAT_FIELDS = (
    "net_gex_bn",
    "flip_cushion",
    "call_wall_distance",
    "put_wall_distance",
    "gex_pct_rank",
    "expected_move_pct",
    "expected_move_consumed",
    "vix",
    "vix9d",
    "vix3m",
    "vvix",
    "vix_contango",
    "rnd_skew",
    "rnd_prob_below_spot",
    "pcr_volume",
    "volume_oi_ratio",
    "rsp_spy_div",
    "sector_align",
    "top10_pressure",
)
_MARKET_INT_FIELDS = ("gamma_sign", "minutes_from_open", "minutes_to_close")


def _coerce_market_context(raw: Any, *, underlying_price: Decimal) -> MarketContext:
    """Build :class:`MarketContext` from a caller-supplied plain dict.

    Same contract as ``_coerce_track_record``: the 0DTE bridge stays decoupled
    from contract classes by passing a dict, and anything malformed degrades to
    an absent field rather than failing the tick. Per-field rather than
    all-or-nothing, because a bad VIX should not cost the agent its view of the
    walls.

    Unknown keys are dropped. A field the caller cannot supply stays ``None``
    and is omitted from the prompt, which the system prompt defines as "not
    observed".
    """
    body: dict[str, Any] = {"underlying_price": underlying_price}
    if isinstance(raw, dict):
        for name in _MARKET_DECIMAL_FIELDS:
            value = raw.get(name)
            if value is not None:
                with suppress(ValueError, TypeError, ArithmeticError):
                    body[name] = Decimal(str(value))
        for name in _MARKET_FLOAT_FIELDS:
            value = raw.get(name)
            if value is not None:
                with suppress(ValueError, TypeError):
                    parsed = float(value)
                    if math.isfinite(parsed):
                        body[name] = parsed
        for name in _MARKET_INT_FIELDS:
            value = raw.get(name)
            if value is not None:
                with suppress(ValueError, TypeError):
                    body[name] = int(value)
        body["technicals"] = _coerce_numeric_map(raw.get("technicals"))
        flags = raw.get("data_quality_flags")
        if isinstance(flags, (list, tuple)):
            body["data_quality_flags"] = tuple(str(f) for f in flags if f)
    return MarketContext(**body)


def _coerce_forecast(raw: Any) -> ForecastContext | None:
    """Build :class:`ForecastContext` from the bridge's forecast dict.

    The bridge has carried a forecast dictionary all along and the prompt
    discarded it. ``None`` when nothing usable is present, so the agent can tell
    "no forecast" from "a forecast with nothing in it".
    """
    if not isinstance(raw, dict) or not raw:
        return None
    horizons = _coerce_numeric_map(
        {k: v for k, v in raw.items() if k not in {"forecast_id", "model_group_id"}}
    )
    if not horizons:
        return None
    return ForecastContext(
        forecast_id=str(raw.get("forecast_id") or ""),
        model_group_id=str(raw.get("model_group_id") or ""),
        horizons=horizons,
    )


def _coerce_numeric_map(raw: Any) -> tuple[tuple[str, float], ...]:
    """Sorted ``(name, finite float)`` pairs; non-numeric entries are dropped."""
    if not isinstance(raw, dict):
        return ()
    out: list[tuple[str, float]] = []
    for key, value in raw.items():
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = float(value)
        except (ValueError, TypeError):
            continue
        if math.isfinite(parsed):
            out.append((str(key), parsed))
    return tuple(sorted(out))


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _market_fingerprint(market: MarketContext | None) -> dict[str, object] | None:
    """Coarse regime markers, at the granularity a decision would actually turn on.

    Gamma sign and the walls are already discrete. The cushion is rounded to
    0.1% of spot, expected-move consumption to 10%, and VIX to a whole
    volatility point — each a step big enough to mean something and big enough
    that ordinary tick noise does not cross it.
    """
    if market is None:
        return None
    return {
        "gamma_sign": market.gamma_sign,
        "flip_cushion": _round(market.flip_cushion, 3),
        "call_wall": str(market.call_wall) if market.call_wall is not None else None,
        "put_wall": str(market.put_wall) if market.put_wall is not None else None,
        "expected_move_consumed": _round(market.expected_move_consumed, 1),
        "vix": _round(market.vix, 0),
    }


def _forecast_fingerprint(forecast: ForecastContext | None) -> dict[str, float] | None:
    """Forecast horizons rounded to two decimals — a 1% probability move is noise."""
    if forecast is None:
        return None
    return {name: round(value, 2) for name, value in forecast.horizons}


def _decision_cache_key(
    *,
    symbol: str,
    session_date: date,
    candidates: tuple[ShadowCandidateView, ...],
    risk_max_size_scalar: float,
    hard_vetoes: tuple[str, ...],
    data_quality: float,
    forecast_uncertainty: float,
    track_record: TrackRecordSummary | None = None,
    market_context: MarketContext | None = None,
    forecast_context: ForecastContext | None = None,
) -> str:
    body: dict[str, object] = {
        "symbol": symbol,
        "session_date": session_date.isoformat(),
        # A regime change must invalidate the unchanged-candidates cache, for
        # the same reason a settled trade does: the same geometry deserves a
        # different answer once the market around it has moved. Deliberately
        # *coarse* — the raw technical vector moves every tick, so keying on it
        # would make the cache never hit and turn every tick into a paid call.
        "market": _market_fingerprint(market_context),
        "forecast": _forecast_fingerprint(forecast_context),
        # A newly settled trade changes the record and must invalidate the
        # unchanged-candidates cache — the whole point of feedback is that the
        # same market can deserve a different answer after a loss.
        "track_record": (
            {
                "n_trades": track_record.n_trades,
                "total_pnl": str(track_record.total_pnl),
            }
            if track_record is not None else None
        ),
        "risk_max_size_scalar": risk_max_size_scalar,
        "hard_vetoes": list(hard_vetoes),
        "data_quality": data_quality,
        "forecast_uncertainty": forecast_uncertainty,
        "candidates": [
            {
                "candidate_id": c.candidate_id,
                "geometry_hash": c.geometry_hash,
                "utility": c.utility,
                "v3_rank": c.v3_rank,
                "fill_probability": c.fill_probability,
                "hard_vetoed": c.hard_vetoed,
                "maximum_loss": str(c.maximum_loss),
            }
            for c in candidates
        ],
    }
    return packet_hash(body)


def _ai_enabled() -> bool:
    """Runtime killswitch. SPY_DER_AI=0 / XAI_ENABLED=0 → deterministic (no HTTP)."""
    for name in ("SPY_DER_AI", "XAI_ENABLED"):
        raw = os.environ.get(name, "").strip().lower()
        if raw in {"0", "false", "off", "no"}:
            return False
    return True


#: Nesting depth of an explicit AI context (see :func:`ai_context`). Non-zero
#: means a caller has declared this work is not a live market tick.
_AI_CONTEXT_DEPTH = 0
_AI_CONTEXT_REASON = ""


@contextmanager
def ai_context(reason: str) -> Iterator[None]:
    """Declare work that may use the AI regardless of market hours.

    The Dojo is the reason this exists. Its timers fire at 06:30 ET — three
    hours before the open — and sparring against recorded and synthetic tape is
    exactly when the model *should* run. A market-hours gate with no exemption
    would silently downgrade every Dojo run to the deterministic agent and
    quietly change what the Dojo measures.

    Re-entrant, and always restores the previous depth, so a raising inner call
    cannot leave the process permanently exempt.
    """
    global _AI_CONTEXT_DEPTH, _AI_CONTEXT_REASON
    previous_reason = _AI_CONTEXT_REASON
    _AI_CONTEXT_DEPTH += 1
    _AI_CONTEXT_REASON = reason or previous_reason
    try:
        yield
    finally:
        _AI_CONTEXT_DEPTH -= 1
        if _AI_CONTEXT_DEPTH <= 0:
            _AI_CONTEXT_DEPTH = 0
            _AI_CONTEXT_REASON = ""
        else:
            _AI_CONTEXT_REASON = previous_reason


def _market_hours_only() -> bool:
    """Whether the market-hours gate applies. `SPY_DER_AI_MARKET_HOURS_ONLY=0` lifts it."""
    raw = os.environ.get("SPY_DER_AI_MARKET_HOURS_ONLY", "").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _market_is_open(now: datetime) -> bool:
    """True when the regular session is open. Unknown calendar → treated as open.

    Failing *open* is deliberate and is the conservative choice for this gate
    specifically: the gate exists to avoid paying for model calls on a dead
    tape, not to enforce safety. Every real safety limit lives in
    :mod:`spy_der.execution.guard`, which is unaffected by this. A calendar
    import failure must not silently downgrade a live trading session to the
    deterministic agent.
    """
    try:
        from spy_der.market_data.calendar import MarketCalendar
    except ImportError:
        return True
    try:
        return MarketCalendar().is_open(now)
    except Exception:
        return True


def _ai_allowed_now(now: datetime) -> tuple[bool, str]:
    """``(allowed, reason)`` for using the paid model on this tick."""
    if not _ai_enabled():
        return False, "killswitch"
    if _AI_CONTEXT_DEPTH > 0:
        return True, f"context:{_AI_CONTEXT_REASON or 'declared'}"
    if not _market_hours_only():
        return True, "gate_disabled"
    if _market_is_open(now):
        return True, "market_open"
    return False, "market_closed"


def _top_k() -> int | None:
    """Optional cap on candidates sent to the model (SPY_DER_AI_TOP_K)."""
    raw = os.environ.get("SPY_DER_AI_TOP_K", "").strip()
    if not raw:
        return 8  # default: keep prompts small on the 60s VPS tick
    try:
        value = int(raw)
    except ValueError:
        return 8
    if value <= 0:
        return None  # 0 / negative => send all
    return value


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _review_gate_passes(resp: AgentDecisionResponse) -> bool:
    """Spend gate for the flagship reviewer: skip it on low-conviction / small
    trades so we only pay for a second-pass review when it can matter.

    ``XAI_REVIEW_MIN_CONFIDENCE`` — skip review when trader confidence is below.
    ``XAI_REVIEW_MIN_SIZE`` — skip review when trader size_scalar is below.
    Both default to 0 (review every TRADE, prior behaviour).
    """
    min_conf = _env_float("XAI_REVIEW_MIN_CONFIDENCE", 0.0)
    if min_conf > 0.0 and float(resp.confidence) < min_conf:
        return False
    min_size = _env_float("XAI_REVIEW_MIN_SIZE", 0.0)
    return not (min_size > 0.0 and float(resp.size_scalar) < min_size)


def _select_candidates(
    candidates: tuple[ShadowCandidateView, ...],
) -> tuple[ShadowCandidateView, ...]:
    limit = _top_k()
    if limit is None or len(candidates) <= limit:
        return candidates
    # Prefer higher utility, then better (lower) v3_rank, preserve stability.
    ranked = sorted(
        candidates,
        key=lambda c: (
            -(c.utility if c.utility is not None else float("-inf")),
            c.v3_rank if c.v3_rank is not None else 10**9,
            c.candidate_id,
        ),
    )
    return tuple(ranked[:limit])


@dataclass(frozen=True, slots=True)
class ShadowCandidateView:
    """Minimal read-only candidate view supplied by 0DTE."""

    candidate_id: str
    family: str
    direction: str
    maximum_loss: Decimal
    capital_required: Decimal
    geometry_hash: str
    expiration: date
    mid_price: Decimal | None = None
    fill_probability: float = 1.0
    utility: float | None = None
    v3_rank: int | None = None
    hard_vetoed: bool = False


@dataclass(frozen=True, slots=True)
class SpyDerShadowDecision:
    action: str  # TRADE | NO_EDGE | ABSTAIN
    candidate_id: str | None
    size_scalar: float
    structure: str | None
    direction: str | None
    confidence: float
    uncertainty: float
    rationale: str
    reason_codes: tuple[str, ...]
    provider: str
    model_id: str
    track: str = PARALLEL_TRACK_ID
    label: str = PARALLEL_TRACK_LABEL
    mode: str = "shadow"
    trader_model_id: str = ""
    reviewer_model_id: str = ""
    reviewer_action: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "track": self.track,
            "label": self.label,
            "source": self.provider,
            "mode": self.mode,
            "action": self.action,
            "structure": self.structure,
            "direction": self.direction,
            "candidate_id": self.candidate_id,
            "size_cap": self.size_scalar,
            "confidence": self.confidence,
            "uncertainty": self.uncertainty,
            "rationale": self.rationale,
            "reason_codes": list(self.reason_codes),
            "model_id": self.model_id,
        }
        if self.trader_model_id:
            payload["trader_model_id"] = self.trader_model_id
        if self.reviewer_model_id:
            payload["reviewer_model_id"] = self.reviewer_model_id
        if self.reviewer_action:
            payload["reviewer_action"] = self.reviewer_action
        return payload


def decide_shadow_tick(
    *,
    snapshot_id: str,
    symbol: str,
    session_date: date,
    underlying_price: Decimal | float,
    candidates: list[ShadowCandidateView] | tuple[ShadowCandidateView, ...],
    now: datetime | None = None,
    agent: DecisionAgent | None = None,
    risk_max_size_scalar: float = 1.0,
    hard_vetoes: tuple[str, ...] = (),
    data_quality: float = 1.0,
    forecast_uncertainty: float = 0.0,
    track_record: dict[str, Any] | None = None,
    market_context: dict[str, Any] | None = None,
    forecast: dict[str, Any] | None = None,
) -> SpyDerShadowDecision:
    """Run AI entry decision over 0DTE shadow candidates.

    ``market_context`` is the measured market state the decision is made
    against — dealer positioning, the volatility surface, flow, breadth and
    per-timeframe technicals — and ``forecast`` is the model forecast the bridge
    has always carried and the prompt used to discard. Both are plain dicts for
    the same reason ``track_record`` is: the 0DTE bridge stays decoupled from
    contract classes. Unknown or malformed fields degrade to absent, and an
    absent field is omitted from the prompt rather than sent as zero.

    ``track_record`` is the agent's own realized paper history (plain dict from
    the caller's trade journal: n_trades / win_rate / total_pnl /
    ev_bias_per_share / by_family / lessons). It reaches the model as packet
    data so decisions are calibrated by past outcomes; malformed input degrades
    to no feedback, never an error.

    Fail-closed: any error building the packet (e.g. out-of-range inputs) or
    running the agent returns an ``ABSTAIN`` decision rather than raising, so a
    single malformed tick never takes down the caller's shadow loop.

    Cost controls (env, no redeploy of callers required after package update):
    - ``SPY_DER_AI=0`` / ``XAI_ENABLED=0`` → deterministic agent (no HTTP)
    - ``XAI_MODEL`` → trader model (default ``grok-4.20-0309-non-reasoning``)
    - ``XAI_REVIEW_MODEL`` / ``XAI_REVIEW_ENABLED`` → reviewer on TRADE only
    - ``SPY_DER_AI_TOP_K`` → max candidates sent (default 8; ``0`` = all)
    - empty candidate set → ``NO_EDGE`` without an API call
    - unchanged candidate fingerprint → reuse prior decision (no API call)
    """
    global _LAST_CACHE_KEY, _LAST_DECISION

    now = now or datetime.now(tz=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    selected = _select_candidates(tuple(candidates))
    agent = agent or _default_trader_agent(now)

    # Empty universe: no edge, and no reason to pay for a model call.
    if not selected:
        decision = SpyDerShadowDecision(
            action="NO_EDGE",
            candidate_id=None,
            size_scalar=0.0,
            structure=None,
            direction=None,
            confidence=0.0,
            uncertainty=0.0,
            rationale="no_shadow_candidates",
            reason_codes=("no_candidates",),
            provider=agent.identity.provider,
            model_id=agent.identity.model_id,
            trader_model_id=agent.identity.model_id,
        )
        _LAST_CACHE_KEY = None
        _LAST_DECISION = decision
        return decision

    record = _coerce_track_record(track_record)
    market = _coerce_market_context(
        market_context, underlying_price=Decimal(str(underlying_price))
    )
    forecast_view = _coerce_forecast(forecast)
    cache_key = _decision_cache_key(
        symbol=symbol,
        session_date=session_date,
        candidates=selected,
        risk_max_size_scalar=risk_max_size_scalar,
        hard_vetoes=hard_vetoes,
        data_quality=data_quality,
        forecast_uncertainty=forecast_uncertainty,
        track_record=record,
        market_context=market,
        forecast_context=forecast_view,
    )
    if (
        _LAST_DECISION is not None
        and _LAST_CACHE_KEY == cache_key
        and os.environ.get("SPY_DER_AI_CACHE", "1").strip().lower()
        not in {"0", "false", "off", "no"}
    ):
        return _LAST_DECISION

    trader_model_id = agent.identity.model_id
    reviewer_model_id = ""
    reviewer_action = ""
    try:
        authority = AiDecisionAuthority(agent, account_id="system_b_grok")
        packet = _build_packet(
            snapshot_id=snapshot_id,
            symbol=symbol,
            session_date=session_date,
            underlying_price=Decimal(str(underlying_price)),
            candidates=selected,
            now=now,
            risk_max_size_scalar=risk_max_size_scalar,
            hard_vetoes=hard_vetoes,
            data_quality=data_quality,
            forecast_uncertainty=forecast_uncertainty,
            track_record=record,
            market_context=market,
            forecast_context=forecast_view,
        )
        result = authority.decide_entry(packet, now=now)
        resp = result.response

        # Second pass: flagship reviewer only when trader wants TRADE — and only
        # when the trade clears the conviction/size spend gate.
        if resp.action is AgentEntryAction.SELECT_CANDIDATE and resp.candidate_id:
            gated = _review_gate_passes(resp)
            reviewer = _reviewer_for(agent) if gated else None
            if not gated:
                reviewer_action = "skipped_below_threshold"
            if reviewer is not None:
                try:
                    review = run_trade_review(reviewer, packet, resp)
                    resp = apply_trade_review(
                        resp,
                        review,
                        risk_max_size_scalar=risk_max_size_scalar,
                    )
                    reviewer_model_id = review.model_id
                    reviewer_action = review.action
                except (ParseError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                    # Fail closed on review errors: do not open on a broken review.
                    resp = AgentDecisionResponse(
                        packet_id=resp.packet_id,
                        packet_hash=resp.packet_hash,
                        action=AgentEntryAction.ABSTAIN,
                        candidate_id=None,
                        size_scalar=0.0,
                        exit_policy_id=None,
                        confidence=0.0,
                        uncertainty=1.0,
                        reason_codes=(*resp.reason_codes, "reviewer_failure"),
                        rationale=f"reviewer_failure:{type(exc).__name__}:{exc}",
                        model_id=reviewer.model_id,
                        prompt_version=resp.prompt_version,
                        geometry_hash=None,
                    )
                    reviewer_model_id = reviewer.model_id
                    reviewer_action = "FAILURE"
    except Exception as exc:  # fail-closed by contract
        return SpyDerShadowDecision(
            action="ABSTAIN",
            candidate_id=None,
            size_scalar=0.0,
            structure=None,
            direction=None,
            confidence=0.0,
            uncertainty=1.0,
            rationale=f"bridge_error:{type(exc).__name__}:{exc}",
            reason_codes=("spy_der_bridge_error",),
            provider=agent.identity.provider,
            model_id=agent.identity.model_id,
            trader_model_id=agent.identity.model_id,
        )

    if resp.action is AgentEntryAction.SELECT_CANDIDATE and resp.candidate_id:
        view = next((c for c in selected if c.candidate_id == resp.candidate_id), None)
        decision = SpyDerShadowDecision(
            action="TRADE",
            candidate_id=resp.candidate_id,
            size_scalar=float(resp.size_scalar),
            structure=view.family if view else None,
            direction=view.direction if view else None,
            confidence=float(resp.confidence),
            uncertainty=float(resp.uncertainty),
            rationale=resp.rationale,
            reason_codes=resp.reason_codes,
            provider=agent.identity.provider,
            model_id=resp.model_id or agent.identity.model_id,
            trader_model_id=trader_model_id,
            reviewer_model_id=reviewer_model_id,
            reviewer_action=reviewer_action,
        )
    else:
        action = "NO_EDGE" if resp.action is AgentEntryAction.NO_EDGE else "ABSTAIN"
        decision = SpyDerShadowDecision(
            action=action,
            candidate_id=None,
            size_scalar=0.0,
            structure=None,
            direction=None,
            confidence=float(resp.confidence),
            uncertainty=float(resp.uncertainty),
            rationale=resp.rationale,
            reason_codes=resp.reason_codes,
            provider=agent.identity.provider,
            model_id=resp.model_id or agent.identity.model_id,
            trader_model_id=trader_model_id,
            reviewer_model_id=reviewer_model_id,
            reviewer_action=reviewer_action,
        )
    _LAST_CACHE_KEY = cache_key
    _LAST_DECISION = decision
    return decision


def parallel_track_payload(decision: SpyDerShadowDecision) -> dict[str, Any]:
    """Dashboard-facing parallel-track card for forecast.parallel_tracks."""
    return decision.as_dict()


def _default_trader_agent(now: datetime | None = None) -> DecisionAgent:
    """Grok when the model is allowed to run, deterministic otherwise.

    The market-hours gate lands here rather than in the units because the
    decision service is an HTTP boundary another process calls: stopping the
    unit out of hours would turn every caller's request into a connection
    error, where returning a deterministic decision degrades cleanly and costs
    nothing. Dojo runs declare themselves via :func:`ai_context` and are exempt.
    """
    if not os.environ.get("XAI_API_KEY"):
        return DeterministicDecisionAgent()
    allowed, _reason = _ai_allowed_now(now or datetime.now(tz=UTC))
    if allowed:
        return GrokDecisionAgent(
            cfg=GrokConfig(model_id=DEFAULT_TRADER_MODEL_ID, auto_http=True)
        )
    return DeterministicDecisionAgent()


def _reviewer_for(trader: DecisionAgent) -> GrokDecisionAgent | None:
    """Attach reviewer sharing the trader transport when possible.

    Only Grok traders are reviewed — mock/deterministic agents skip the
    second pass so offline tests and $0 fallback stay single-shot.
    """
    if not isinstance(trader, GrokDecisionAgent):
        return None
    return make_default_reviewer(
        transport=trader.transport,
        api_key=trader.api_key or None,
    )


def _build_packet(
    *,
    snapshot_id: str,
    symbol: str,
    session_date: date,
    underlying_price: Decimal,
    candidates: tuple[ShadowCandidateView, ...],
    now: datetime,
    risk_max_size_scalar: float,
    hard_vetoes: tuple[str, ...],
    data_quality: float,
    forecast_uncertainty: float,
    track_record: TrackRecordSummary | None = None,
    market_context: MarketContext | None = None,
    forecast_context: ForecastContext | None = None,
) -> AgentDecisionPacket:
    views = tuple(
        AgentCandidateView(
            candidate_id=c.candidate_id,
            family=c.family,
            direction=c.direction,
            expiration=c.expiration,
            legs_summary=(),
            maximum_loss=Decimal(str(c.maximum_loss)),
            capital_required=Decimal(str(c.capital_required)),
            # Placeholder when 0DTE supplies no real geometry hash (shadow-only
            # display value; not a cryptographic hash of the structure).
            geometry_hash=c.geometry_hash or f"sha256:{c.candidate_id}",
            mid_price=c.mid_price,
            fill_probability=float(c.fill_probability),
            candidate_utility=c.utility,
            v3_rank=c.v3_rank,
            hard_vetoed=c.hard_vetoed,
            evidence_ids=(f"cand:{c.candidate_id}",),
        )
        for c in candidates
    )
    deployment_id = "spy-der-zerodte-bridge"
    # Body mirrors the canonical builder (spy_der.agents.packet) so the
    # packet_id/hash bind candidate geometry, not just IDs.
    body = {
        "snapshot_id": snapshot_id,
        "symbol": symbol,
        "session_date": session_date.isoformat(),
        "candidate_ids": [v.candidate_id for v in views],
        "geometry_hashes": [v.geometry_hash for v in views],
        "risk_max_size_scalar": risk_max_size_scalar,
        "hard_vetoes": list(hard_vetoes),
        "deployment_id": deployment_id,
        # Same rule as the canonical builder: the context is part of what makes
        # this decision distinct, so it belongs in the hash.
        "market_context": (
            to_canonical_json(market_context) if market_context is not None else None
        ),
        "forecast_context": (
            to_canonical_json(forecast_context) if forecast_context is not None else None
        ),
    }
    # Security: processed-output body must never carry secrets, same guard the
    # canonical builder applies before hashing.
    assert_no_secrets(body)
    ph = packet_hash(body)
    return AgentDecisionPacket(
        packet_id=make_packet_id(snapshot_id, ph),
        packet_hash=ph,
        created_at=now,
        expires_at=now + timedelta(seconds=45),
        snapshot_summary=SnapshotSummary(
            snapshot_id=snapshot_id,
            symbol=symbol,
            session_date=session_date,
            underlying_price=underlying_price,
        ),
        candidates=views,
        risk_max_size_scalar=risk_max_size_scalar,
        hard_vetoes=hard_vetoes,
        approved_exit_policies=(
            ExitPolicySummary(ApprovedExitPolicyId.TARGET_AND_STOP.value, "target_and_stop"),
            ExitPolicySummary(ApprovedExitPolicyId.EOD_EXIT.value, "eod"),
        ),
        deployment_context=DeploymentContext(
            deployment_id=deployment_id,
            mode="shadow",
        ),
        data_quality=data_quality,
        forecast_uncertainty=forecast_uncertainty,
        evidence_ids=tuple(sorted({eid for v in views for eid in v.evidence_ids})),
        track_record=track_record,
        market_context=market_context,
        forecast_context=forecast_context,
    )
