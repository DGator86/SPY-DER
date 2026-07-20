"""V2 forecast contracts (master spec §24, §30).

Migrated from System A ``prediction/contracts.py`` (0DTE @ de4a6e7)
``PredictionBundle``, renamed to the canonical SPY-DER ``MarketForecastBundle``.

Rules:
* every probability is in [0, 1] or None (None = required inputs unavailable);
* returns are decimal LOG returns;
* the bundle carries no policy fields (no selected structure, conviction, or
  candidate score) — forecasting stays separate from policy (spec §3).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from spy_der.contracts.common import (
    SCHEMA_VERSION,
    ErrorCode,
    ValidationError,
    content_hash,
    deterministic_id,
    require_probability,
)

__all__ = [
    "FEATURE_VERSION",
    "HORIZONS",
    "LABEL_VERSION",
    "MarketForecastBundle",
]

FEATURE_VERSION = "v2.0.0"
LABEL_VERSION = "v2.0.0"
HORIZONS: tuple[str, ...] = ("5m", "15m", "30m", "60m", "close")

_BOUNDED_FIELDS = frozenset(
    {
        "uncertainty",
        "data_quality",
        "feature_coverage",
        "ood_score",
        "ood_percentile",
        "calibration_support",
        "regime_uncertainty",
    }
)


def _require_optional_probability(value: float | None, name: str) -> None:
    if value is None:
        return
    require_probability(value, name)


@dataclass(frozen=True, slots=True)
class MarketForecastBundle:
    """Canonical multi-horizon forecast object (spec §30)."""

    snapshot_id: str
    ts: str
    session_date: str
    symbol: str = "SPY"
    schema_version: str = SCHEMA_VERSION
    forecast_id: str = ""
    deployment_id: str = ""
    model_group_id: str = ""
    feature_version: str = FEATURE_VERSION
    label_version: str = LABEL_VERSION
    # Convenience alias used by synthesis/smoke tests; prefer model_group_id.
    model_version: str = ""

    p_up_5m: float | None = None
    p_up_15m: float | None = None
    p_up_30m: float | None = None
    p_up_60m: float | None = None
    p_up_close: float | None = None

    expected_return_15m: float | None = None
    expected_return_30m: float | None = None
    expected_return_60m: float | None = None
    expected_return_close: float | None = None
    return_q10_30m: float | None = None
    return_q50_30m: float | None = None
    return_q90_30m: float | None = None
    return_q10_close: float | None = None
    return_q50_close: float | None = None
    return_q90_close: float | None = None

    expected_realized_move_30m: float | None = None
    expected_realized_move_close: float | None = None
    p_range_survive_15m: float | None = None
    p_range_survive_30m: float | None = None
    p_range_survive_60m: float | None = None
    p_range_survive_close: float | None = None

    p_touch_call_wall_30m: float | None = None
    p_touch_put_wall_30m: float | None = None
    p_touch_gamma_flip_30m: float | None = None
    p_touch_call_wall_close: float | None = None
    p_touch_put_wall_close: float | None = None
    p_cross_gamma_flip_close: float | None = None

    p_call_wall_first: float | None = None
    p_put_wall_first: float | None = None
    p_neither_wall_close: float | None = None

    uncertainty: float | None = None
    data_quality: float | None = None
    feature_coverage: float | None = None
    model_versions: dict[str, str] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    fallback_state: str = ""
    content_hash: str = ""

    # V3 extension slots (optional; Phase 6+)
    uncertainty_components: dict[str, float | None] = field(default_factory=dict)
    uncertainty_reasons: tuple[str, ...] = ()
    ood_score: float | None = None
    ood_percentile: float | None = None
    calibration_support: float | None = None
    ensemble_size: int | None = None
    regime_probabilities: dict[str, float] = field(default_factory=dict)
    regime_uncertainty: float | None = None
    dominant_regime: str | None = None
    return_distributions: dict[str, Any] = field(default_factory=dict)
    competing_risk_forecasts: dict[str, Any] = field(default_factory=dict)
    path_forecasts: dict[str, Any] = field(default_factory=dict)
    ensemble_forecasts: dict[str, Any] = field(default_factory=dict)
    structural_state_version: str | None = None
    artifact_hashes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise ValidationError(ErrorCode.MISSING_REQUIRED_INPUT, "snapshot_id is required")
        for f in fields(self):
            if f.name.startswith("p_") or f.name in _BOUNDED_FIELDS:
                _require_optional_probability(getattr(self, f.name), f.name)
        for key, value in self.uncertainty_components.items():
            _require_optional_probability(value, f"uncertainty_components[{key}]")
        for key, value in self.regime_probabilities.items():
            require_probability(value, f"regime_probabilities[{key}]")

        model_version = self.model_version or self.model_group_id or self.feature_version
        object.__setattr__(self, "model_version", model_version)

        if not self.forecast_id:
            object.__setattr__(
                self,
                "forecast_id",
                deterministic_id(
                    "fcst",
                    self.snapshot_id,
                    self.feature_version,
                    self.label_version,
                    self.model_group_id or self.model_version,
                    self.p_up_30m,
                    self.expected_return_30m,
                ),
            )
        if not self.content_hash:
            identity = {
                f.name: getattr(self, f.name)
                for f in fields(self)
                if f.name not in {"forecast_id", "content_hash"}
            }
            object.__setattr__(self, "content_hash", content_hash(identity))

    @property
    def prob_up(self) -> float:
        """Backward-compatible alias: primary horizon up-probability."""
        if self.p_up_30m is not None:
            return self.p_up_30m
        for value in (self.p_up_15m, self.p_up_60m, self.p_up_close, self.p_up_5m):
            if value is not None:
                return value
        return 0.0

    @property
    def prob_down(self) -> float:
        return 1.0 - self.prob_up

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MarketForecastBundle:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
