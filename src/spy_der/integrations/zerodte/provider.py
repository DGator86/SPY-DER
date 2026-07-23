"""Decision provider for 0DTE parallel paper track + dashboard panel.

0DTE calls `decide_shadow_tick` each tick with read-only shadow candidates.
SPY-DER AI (Grok by default, Deterministic fallback) selects among them.
Live broker routing stays disabled — paper/shadow only.
"""

from __future__ import annotations

import os
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
    SnapshotSummary,
    TrackRecordSummary,
    make_packet_id,
    packet_hash,
)
from spy_der.contracts.positions import ApprovedExitPolicyId

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
) -> str:
    body: dict[str, object] = {
        "symbol": symbol,
        "session_date": session_date.isoformat(),
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
) -> SpyDerShadowDecision:
    """Run AI entry decision over 0DTE shadow candidates.

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
    agent = agent or _default_trader_agent()

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
    cache_key = _decision_cache_key(
        symbol=symbol,
        session_date=session_date,
        candidates=selected,
        risk_max_size_scalar=risk_max_size_scalar,
        hard_vetoes=hard_vetoes,
        data_quality=data_quality,
        forecast_uncertainty=forecast_uncertainty,
        track_record=record,
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


def _default_trader_agent() -> DecisionAgent:
    # Prefer Grok when an API key is present AND the runtime AI killswitch is on.
    if _ai_enabled() and os.environ.get("XAI_API_KEY"):
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
    )
