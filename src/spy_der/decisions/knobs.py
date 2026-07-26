"""Champion / challenger decision knobs — one implementation, two callers.

The Dojo scores a challenger by re-deciding recorded tape with a config delta
applied (:class:`spy_der.dojo.authority.ChallengerDecisionAuthority`). Once that
delta is promoted it has to change *live* decisions the same way, or the Dojo
validated something that never ran. Both paths therefore go through
:class:`DecisionKnobs`: the trial and production apply identical arithmetic.

Knobs are deliberately conservative — every one of them can only make the system
trade *less*:

``risk_max_size_scalar``
    A ceiling on the caller's size scalar, never a lift. A promoted config can
    shrink size; it can never grow it past what risk already allowed.
``min_confidence``
    Confidence floor below which a TRADE becomes an ABSTAIN.
``prefer_abstain_on_ood``
    Adds a hard veto when forecast uncertainty is at or above ``ood_threshold``,
    so out-of-distribution ticks stand down.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["KNOB_NAMES", "OOD_VETO", "DecisionKnobs", "actionable_knobs"]

#: Veto code raised when ``prefer_abstain_on_ood`` stands a tick down.
OOD_VETO = "challenger_ood_abstain"

#: Knobs that actually change a decision. A hypothesis whose change touches
#: none of these is not promotable — there would be nothing to enact.
#: ``ood_threshold`` only qualifies ``prefer_abstain_on_ood``; on its own it
#: leaves :attr:`DecisionKnobs.is_noop` true and so cannot promote by itself.
KNOB_NAMES = frozenset(
    {
        "risk_max_size_scalar",
        "min_confidence",
        "prefer_abstain_on_ood",
        "ood_threshold",
    }
)

_TRADE_ACTIONS = frozenset({"TRADE", "SELECT_CANDIDATE"})


def _as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # drop NaN


@dataclass(frozen=True, slots=True)
class DecisionKnobs:
    """Config deltas applied on top of a decision, in a single place."""

    risk_max_size_scalar: float | None = None
    min_confidence: float = 0.0
    prefer_abstain_on_ood: bool = False
    ood_threshold: float = 0.75

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> DecisionKnobs:
        """Build from a config dict; unknown or malformed entries are ignored."""
        if not raw:
            return cls()
        risk = _as_float(raw.get("risk_max_size_scalar"))
        if risk is not None:
            risk = max(0.0, min(1.0, risk))
        floor = _as_float(raw.get("min_confidence")) or 0.0
        threshold = _as_float(raw.get("ood_threshold"))
        return cls(
            risk_max_size_scalar=risk,
            min_confidence=max(0.0, min(1.0, floor)),
            prefer_abstain_on_ood=bool(raw.get("prefer_abstain_on_ood", False)),
            ood_threshold=0.75 if threshold is None else threshold,
        )

    @property
    def is_noop(self) -> bool:
        """True when these knobs cannot change any decision."""
        return (
            self.risk_max_size_scalar is None
            and self.min_confidence <= 0.0
            and not self.prefer_abstain_on_ood
        )

    def effective_risk(self, packet_risk: float) -> float:
        """Cap the caller's size scalar — knobs shrink risk, never raise it."""
        base = max(0.0, min(1.0, float(packet_risk)))
        if self.risk_max_size_scalar is None:
            return base
        return min(base, self.risk_max_size_scalar)

    def effective_hard_vetoes(
        self,
        hard_vetoes: tuple[str, ...],
        forecast_uncertainty: float,
    ) -> tuple[str, ...]:
        """Append the OOD veto when uncertainty crosses the threshold."""
        if not self.prefer_abstain_on_ood:
            return tuple(hard_vetoes)
        if float(forecast_uncertainty) < self.ood_threshold:
            return tuple(hard_vetoes)
        if OOD_VETO in hard_vetoes:
            return tuple(hard_vetoes)
        return (*hard_vetoes, OOD_VETO)

    def apply_confidence_floor(
        self,
        action: str,
        candidate_id: str | None,
        confidence: float,
    ) -> tuple[str, str | None]:
        """Downgrade an under-confident TRADE to ABSTAIN."""
        if action not in _TRADE_ACTIONS or self.min_confidence <= 0.0:
            return action, candidate_id
        if float(confidence) >= self.min_confidence:
            return action, candidate_id
        return "ABSTAIN", None

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_max_size_scalar": self.risk_max_size_scalar,
            "min_confidence": self.min_confidence,
            "prefer_abstain_on_ood": self.prefer_abstain_on_ood,
            "ood_threshold": self.ood_threshold,
        }


def actionable_knobs(change: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return only the entries of ``change`` that a decision actually reads."""
    if not change:
        return {}
    return {k: v for k, v in change.items() if k in KNOB_NAMES}
