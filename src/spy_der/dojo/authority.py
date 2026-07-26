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
from spy_der.decisions.champion import load_champion_knobs
from spy_der.decisions.knobs import DecisionKnobs
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
    """Champion / active SPY-DER decision path (optional injected agent).

    ``knobs`` carries whatever ``champion.json`` currently has promoted, so this
    authority scores what production actually does. Without it the Dojo would
    compare every challenger against a pre-promotion champion and could promote
    a config that is worse than the one already live.
    """

    agent: DecisionAgent | None = None
    authority_name: str = "champion"
    knobs: DecisionKnobs | None = None

    @property
    def name(self) -> str:
        return self.authority_name

    def decide(self, market: MarketPacket) -> DecisionRecord:
        knobs = self.knobs or DecisionKnobs()
        shadow = decide_shadow_tick(
            snapshot_id=market.snapshot_id,
            symbol=market.symbol,
            session_date=market.session_date,
            underlying_price=market.underlying_price,
            candidates=_views(market),
            now=market.generated_at or datetime.now(UTC),
            agent=self.agent,
            risk_max_size_scalar=knobs.effective_risk(market.risk_max_size_scalar),
            hard_vetoes=knobs.effective_hard_vetoes(
                market.hard_vetoes, market.forecast_uncertainty
            ),
            data_quality=market.data_quality,
            forecast_uncertainty=market.forecast_uncertainty,
            track_record=market.track_record or None,
        )
        action, candidate_id = knobs.apply_confidence_floor(
            shadow.action, shadow.candidate_id, float(shadow.confidence)
        )
        return _to_dojo_decision(
            snapshot_id=market.snapshot_id,
            action=action,
            candidate_id=candidate_id,
            confidence=shadow.confidence,
            direction=(
                shadow.direction
                if action in {"TRADE", "SELECT_CANDIDATE"}
                else None
            ),
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
    """Challenger authority — deterministic path with hypothesis config deltas.

    The deltas are applied by :class:`spy_der.decisions.knobs.DecisionKnobs`,
    the same object the live decision service applies to a promoted
    ``champion.json``. That shared path is what makes a Dojo promotion trial
    predictive: the arithmetic scored here is the arithmetic that ships.
    """

    changes: dict[str, Any]
    authority_name: str = "challenger"
    knobs: DecisionKnobs | None = None
    _agent: DeterministicDecisionAgent | None = None

    def __post_init__(self) -> None:
        if self._agent is None:
            self._agent = DeterministicDecisionAgent()
        if self.knobs is None:
            self.knobs = DecisionKnobs.from_mapping(self.changes)

    @property
    def name(self) -> str:
        return self.authority_name

    @property
    def min_confidence(self) -> float:
        assert self.knobs is not None
        return self.knobs.min_confidence

    def decide(self, market: MarketPacket) -> DecisionRecord:
        assert self._agent is not None
        assert self.knobs is not None
        shadow = decide_shadow_tick(
            snapshot_id=market.snapshot_id,
            symbol=market.symbol,
            session_date=market.session_date,
            underlying_price=market.underlying_price,
            candidates=_views(market),
            now=market.generated_at or datetime.now(UTC),
            agent=self._agent,
            risk_max_size_scalar=self.knobs.effective_risk(market.risk_max_size_scalar),
            hard_vetoes=self.knobs.effective_hard_vetoes(
                market.hard_vetoes, market.forecast_uncertainty
            ),
            data_quality=market.data_quality,
            forecast_uncertainty=market.forecast_uncertainty,
            track_record=market.track_record or None,
        )
        confidence = float(shadow.confidence)
        action, candidate_id = self.knobs.apply_confidence_floor(
            shadow.action, shadow.candidate_id, confidence
        )
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
    configs_dir: str | None = None,
) -> dict[str, DecisionAuthority]:
    """Build the standard champion / baseline / optional challenger set.

    The champion is seeded with the promoted ``champion.json`` knobs from
    ``configs_dir`` so it decides exactly as the live service does; the baseline
    stays knob-free, which is what makes forward transfer meaningful.
    """
    authorities: dict[str, DecisionAuthority] = {
        "champion": ActiveDecisionAuthority(
            agent=champion_agent or DeterministicDecisionAgent(),
            authority_name="champion",
            knobs=load_champion_knobs(configs_dir),
        ),
        "baseline": DeterministicBaselineAuthority(),
    }
    if challenger_changes:
        authorities["challenger"] = ChallengerDecisionAuthority(changes=challenger_changes)
    return authorities
