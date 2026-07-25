"""Decision authorities used by the Dojo for champion / challenger / baseline.

These wrap the same MarketPacket → decision path as the HTTP service, without
importing 0DTE internals. Offline Dojo runs default to the deterministic agent
so training never spends Grok tokens unless an authority is explicitly injected.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from spy_der.agents.deterministic import DeterministicDecisionAgent
from spy_der.agents.protocols import DecisionAgent
from spy_der.contracts.integration import MarketPacket
from spy_der.decisions.shadow import (
    ShadowCandidateView,
    decide_shadow_tick,
)
from spy_der.dojo.decisions import DojoDecision
from spy_der.dojo.protocols import DecisionAuthority, DecisionRecord

__all__ = [
    "ActiveDecisionAuthority",
    "ChallengerDecisionAuthority",
    "DecisionAuthority",
    "DeterministicBaselineAuthority",
    "default_authorities",
]


def _views(market: MarketPacket) -> list[ShadowCandidateView]:
    return [
        ShadowCandidateView(
            candidate_id=c.candidate_id,
            family=c.family,
            direction=c.direction,
            maximum_loss=c.maximum_loss,
            capital_required=c.capital_required,
            geometry_hash=c.geometry_hash,
            expiration=c.expiration,
            mid_price=c.mid_price,
            fill_probability=c.fill_probability,
            utility=c.utility,
            v3_rank=c.v3_rank,
            hard_vetoed=c.hard_vetoed,
        )
        for c in market.candidates
    ]


def _to_dojo_decision(
    *,
    snapshot_id: str,
    action: str,
    candidate_id: str | None,
    confidence: float,
    direction: str | None,
    authority: str,
) -> DecisionRecord:
    # Normalize SELECT_CANDIDATE → TRADE for evaluation matching.
    normalized = "TRADE" if action == "SELECT_CANDIDATE" else action
    return DojoDecision(
        snapshot_id=snapshot_id,
        action=normalized,
        candidate_id=candidate_id,
        confidence=float(confidence),
        direction=direction,
        authority=authority,
    )


@dataclass
class ActiveDecisionAuthority:
    """Champion / active SPY-DER decision path (optional injected agent)."""

    agent: DecisionAgent | None = None
    authority_name: str = "champion"

    @property
    def name(self) -> str:
        return self.authority_name

    def decide(self, market: MarketPacket) -> DecisionRecord:
        shadow = decide_shadow_tick(
            snapshot_id=market.snapshot_id,
            symbol=market.symbol,
            session_date=market.session_date,
            underlying_price=market.underlying_price,
            candidates=_views(market),
            now=market.generated_at or datetime.now(UTC),
            agent=self.agent,
            risk_max_size_scalar=market.risk_max_size_scalar,
            hard_vetoes=market.hard_vetoes,
            data_quality=market.data_quality,
            forecast_uncertainty=market.forecast_uncertainty,
            track_record=market.track_record or None,
        )
        return _to_dojo_decision(
            snapshot_id=market.snapshot_id,
            action=shadow.action,
            candidate_id=shadow.candidate_id,
            confidence=shadow.confidence,
            direction=shadow.direction,
            authority=self.name,
        )


@dataclass
class DeterministicBaselineAuthority:
    """Deterministic policy/utility baseline — no LLM."""

    authority_name: str = "baseline"
    _agent: DeterministicDecisionAgent | None = None

    def __post_init__(self) -> None:
        if self._agent is None:
            self._agent = DeterministicDecisionAgent()

    @property
    def name(self) -> str:
        return self.authority_name

    def decide(self, market: MarketPacket) -> DecisionRecord:
        assert self._agent is not None
        # Apply size derate if challenger-style knobs were copied here.
        risk = market.risk_max_size_scalar
        shadow = decide_shadow_tick(
            snapshot_id=market.snapshot_id,
            symbol=market.symbol,
            session_date=market.session_date,
            underlying_price=market.underlying_price,
            candidates=_views(market),
            now=market.generated_at or datetime.now(UTC),
            agent=self._agent,
            risk_max_size_scalar=risk,
            hard_vetoes=market.hard_vetoes,
            data_quality=market.data_quality,
            forecast_uncertainty=market.forecast_uncertainty,
            track_record=market.track_record or None,
        )
        return _to_dojo_decision(
            snapshot_id=market.snapshot_id,
            action=shadow.action,
            candidate_id=shadow.candidate_id,
            confidence=shadow.confidence,
            direction=shadow.direction,
            authority=self.name,
        )


@dataclass
class ChallengerDecisionAuthority:
    """Challenger authority — deterministic path with hypothesis config deltas."""

    changes: dict[str, Any]
    authority_name: str = "challenger"
    min_confidence: float = 0.0
    _agent: DeterministicDecisionAgent | None = None

    def __post_init__(self) -> None:
        if self._agent is None:
            self._agent = DeterministicDecisionAgent()
        raw = self.changes.get("min_confidence")
        if raw is not None:
            self.min_confidence = float(raw)

    @property
    def name(self) -> str:
        return self.authority_name

    def decide(self, market: MarketPacket) -> DecisionRecord:
        assert self._agent is not None
        risk = float(
            self.changes.get("risk_max_size_scalar", market.risk_max_size_scalar)
        )
        # Prefer abstain on OOD: raise effective hard veto when flagged.
        hard = list(market.hard_vetoes)
        if self.changes.get("prefer_abstain_on_ood") and market.forecast_uncertainty >= 0.75:
            hard.append("challenger_ood_abstain")
        shadow = decide_shadow_tick(
            snapshot_id=market.snapshot_id,
            symbol=market.symbol,
            session_date=market.session_date,
            underlying_price=market.underlying_price,
            candidates=_views(market),
            now=market.generated_at or datetime.now(UTC),
            agent=self._agent,
            risk_max_size_scalar=max(0.0, min(1.0, risk)),
            hard_vetoes=tuple(hard),
            data_quality=market.data_quality,
            forecast_uncertainty=market.forecast_uncertainty,
            track_record=market.track_record or None,
        )
        action = shadow.action
        candidate_id = shadow.candidate_id
        confidence = float(shadow.confidence)
        if (
            action in {"TRADE", "SELECT_CANDIDATE"}
            and self.min_confidence > 0.0
            and confidence < self.min_confidence
        ):
            action = "ABSTAIN"
            candidate_id = None
        return _to_dojo_decision(
            snapshot_id=market.snapshot_id,
            action=action,
            candidate_id=candidate_id,
            confidence=confidence,
            direction=shadow.direction if action in {"TRADE", "SELECT_CANDIDATE"} else None,
            authority=self.name,
        )


def default_authorities(
    *,
    champion_agent: DecisionAgent | None = None,
    challenger_changes: dict[str, Any] | None = None,
) -> dict[str, DecisionAuthority]:
    """Build the standard champion / baseline / optional challenger set."""
    authorities: dict[str, DecisionAuthority] = {
        "champion": ActiveDecisionAuthority(
            agent=champion_agent or DeterministicDecisionAgent(),
            authority_name="champion",
        ),
        "baseline": DeterministicBaselineAuthority(),
    }
    if challenger_changes:
        authorities["challenger"] = ChallengerDecisionAuthority(changes=challenger_changes)
    return authorities
