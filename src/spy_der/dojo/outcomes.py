"""Outcome resolution helpers for recorded and synthetic Dojo packets."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from spy_der.contracts.integration import (
    OUTCOME_PACKET_SCHEMA,
    MarketPacket,
    OutcomePacket,
    outcome_packet_from_dict,
)
from spy_der.dojo.protocols import MarketExperienceProvider

__all__ = [
    "collect_outcomes",
    "outcome_from_market_labels",
    "outcomes_from_packets",
]


def outcome_from_market_labels(packet: MarketPacket) -> OutcomePacket | None:
    """Build an OutcomePacket from labels embedded in MarketPacket.forecast.

    Supported shapes (any one is enough)::

        forecast["outcome"] = { ... OutcomePacket fields ... }
        forecast["labels"] = {
            "realized_pnl": 0.1,
            "candidate_id": "c1",          # optional
            "true_direction": "bullish",   # optional
            "action": "TRADE",             # optional
        }
        forecast["realized_pnl"] = 0.1
    """
    forecast = packet.forecast or {}
    raw_outcome = forecast.get("outcome")
    if isinstance(raw_outcome, dict):
        body = {
            "schema_version": OUTCOME_PACKET_SCHEMA,
            "snapshot_id": packet.snapshot_id,
            "session_date": packet.session_date.isoformat(),
            "symbol": packet.symbol,
            **raw_outcome,
        }
        body.setdefault("snapshot_id", packet.snapshot_id)
        try:
            return outcome_packet_from_dict(body)
        except (ValueError, TypeError, KeyError):
            pass

    labels = forecast.get("labels")
    label_map: dict[str, Any] = labels if isinstance(labels, dict) else {}
    pnl_raw = label_map.get("realized_pnl", forecast.get("realized_pnl"))
    if pnl_raw is None:
        return None

    candidate_id = label_map.get("candidate_id") or forecast.get("candidate_id")
    if candidate_id is None and len(packet.candidates) == 1:
        candidate_id = packet.candidates[0].candidate_id

    true_direction = label_map.get("true_direction") or forecast.get("true_direction")
    action = str(label_map.get("action") or forecast.get("action") or "SETTLED")
    labels_out: dict[str, Any] = {}
    if true_direction is not None:
        labels_out["true_direction"] = str(true_direction)
    # Preserve per-candidate pnl map when present (for regret).
    by_cand = label_map.get("realized_pnl_by_candidate") or forecast.get(
        "realized_pnl_by_candidate"
    )
    if isinstance(by_cand, dict):
        labels_out["realized_pnl_by_candidate"] = {
            str(k): str(v) for k, v in by_cand.items()
        }

    return OutcomePacket(
        schema_version=OUTCOME_PACKET_SCHEMA,
        snapshot_id=packet.snapshot_id,
        session_date=packet.session_date,
        symbol=packet.symbol,
        candidate_id=str(candidate_id) if candidate_id is not None else None,
        action=action,
        realized_pnl=Decimal(str(pnl_raw)),
        settled=True,
        labels=labels_out,
        settled_at=packet.generated_at or datetime.now(UTC),
    )


def outcomes_from_packets(packets: Iterable[MarketPacket]) -> list[OutcomePacket]:
    out: list[OutcomePacket] = []
    for packet in packets:
        outcome = outcome_from_market_labels(packet)
        if outcome is not None:
            out.append(outcome)
    return out


def collect_outcomes(
    provider: MarketExperienceProvider | None,
    packets: Sequence[MarketPacket],
) -> list[OutcomePacket]:
    """Prefer provider outcomes; fall back to packet-embedded labels."""
    out: list[OutcomePacket] = []
    seen: set[str] = set()
    if provider is not None:
        for packet in packets:
            outcome = provider.outcome(packet.snapshot_id)
            if outcome is not None:
                out.append(outcome)
                seen.add(packet.snapshot_id)
    for packet in packets:
        if packet.snapshot_id in seen:
            continue
        outcome = outcome_from_market_labels(packet)
        if outcome is not None:
            out.append(outcome)
    return out
