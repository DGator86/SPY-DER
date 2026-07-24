"""Concrete decision records produced by Dojo authorities."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DojoDecision"]


@dataclass(frozen=True, slots=True)
class DojoDecision:
    """DecisionRecord implementation used across Dojo phases."""

    snapshot_id: str
    action: str
    candidate_id: str | None
    confidence: float = 0.0
    direction: str | None = None
    authority: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "action": self.action,
            "candidate_id": self.candidate_id,
            "confidence": self.confidence,
            "direction": self.direction,
            "authority": self.authority,
        }
