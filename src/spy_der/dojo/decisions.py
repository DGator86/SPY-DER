"""Dojo decision lanes — champion / challenger / deterministic baseline.

Runs SPY-DER decision authority over MarketPackets without importing 0DTE.
Champion uses the live shadow decision path; baseline and challenger use
deterministic heuristics so Dojo scoring stays reproducible offline.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from spy_der.contracts.integration import MarketCandidateView, MarketPacket
from spy_der.integrations.zerodte.provider import (
    ShadowCandidateView,
    SpyDerShadowDecision,
    decide_shadow_tick,
)

__all__ = [
    "DecisionRole",
    "DojoDecision",
    "decide_on_packets",
    "rank_candidates",
]


class DecisionRole(StrEnum):
    CHAMPION = "champion"
    CHALLENGER = "challenger"
    BASELINE = "baseline"


@dataclass(frozen=True, slots=True)
class DojoDecision:
    """Concrete DecisionRecord used by CandidateEvaluator."""

    snapshot_id: str
    action: str
    candidate_id: str | None
    role: str = DecisionRole.CHAMPION.value
    direction: str | None = None
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "action": self.action,
            "candidate_id": self.candidate_id,
            "role": self.role,
            "direction": self.direction,
            "confidence": self.confidence,
        }


def rank_candidates(
    candidates: Sequence[MarketCandidateView],
    *,
    prefer_second: bool = False,
) -> list[MarketCandidateView]:
    """Utility-first ranking; optionally prefer the second-best for challenger."""
    ranked = sorted(
        [c for c in candidates if not c.hard_vetoed],
        key=lambda c: (
            -(c.utility if c.utility is not None else float("-inf")),
            c.candidate_id,
        ),
    )
    if prefer_second and len(ranked) >= 2:
        return [ranked[1], ranked[0], *ranked[2:]]
    return ranked


def _to_shadow_views(
    candidates: Sequence[MarketCandidateView],
) -> tuple[ShadowCandidateView, ...]:
    return tuple(
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
        for c in candidates
    )


def _direction_for(
    packet: MarketPacket, candidate_id: str | None
) -> str | None:
    if not candidate_id:
        return None
    for cand in packet.candidates:
        if cand.candidate_id == candidate_id:
            return cand.direction
    return None


def _deterministic_pick(
    packet: MarketPacket,
    *,
    role: DecisionRole,
    prefer_second: bool = False,
) -> DojoDecision:
    if packet.hard_vetoes:
        return DojoDecision(
            snapshot_id=packet.snapshot_id,
            action="ABSTAIN",
            candidate_id=None,
            role=role.value,
            confidence=0.0,
        )
    ranked = rank_candidates(packet.candidates, prefer_second=prefer_second)
    if not ranked:
        return DojoDecision(
            snapshot_id=packet.snapshot_id,
            action="NO_EDGE",
            candidate_id=None,
            role=role.value,
            confidence=0.0,
        )
    top = ranked[0]
    return DojoDecision(
        snapshot_id=packet.snapshot_id,
        action="SELECT_CANDIDATE",
        candidate_id=top.candidate_id,
        role=role.value,
        direction=top.direction,
        confidence=0.5,
    )


def _champion_decide(packet: MarketPacket) -> DojoDecision:
    """Active SPY-DER decision authority (shadow tick path)."""
    now = packet.generated_at or datetime.now(UTC)
    shadow: SpyDerShadowDecision = decide_shadow_tick(
        snapshot_id=packet.snapshot_id,
        symbol=packet.symbol,
        session_date=packet.session_date,
        underlying_price=Decimal(str(packet.underlying_price)),
        candidates=_to_shadow_views(packet.candidates),
        now=now,
        risk_max_size_scalar=packet.risk_max_size_scalar,
        hard_vetoes=packet.hard_vetoes,
        data_quality=packet.data_quality,
        forecast_uncertainty=packet.forecast_uncertainty,
        track_record=packet.track_record or None,
    )
    action = shadow.action
    if action == "SELECT_CANDIDATE":
        action = "TRADE"
    return DojoDecision(
        snapshot_id=packet.snapshot_id,
        action=action,
        candidate_id=shadow.candidate_id,
        role=DecisionRole.CHAMPION.value,
        direction=shadow.direction or _direction_for(packet, shadow.candidate_id),
        confidence=float(shadow.confidence),
    )


def decide_on_packets(
    packets: Iterable[MarketPacket],
    *,
    role: DecisionRole | str = DecisionRole.CHAMPION,
) -> list[DojoDecision]:
    """Produce decisions for a lane over a sequence of market packets."""
    lane = DecisionRole(role)
    out: list[DojoDecision] = []
    for packet in packets:
        if lane is DecisionRole.CHAMPION:
            out.append(_champion_decide(packet))
        elif lane is DecisionRole.CHALLENGER:
            out.append(
                _deterministic_pick(packet, role=lane, prefer_second=True)
            )
        else:
            out.append(_deterministic_pick(packet, role=lane, prefer_second=False))
    return out
