"""Alpha V2 market-model contracts.

These contracts implement the hard boundary between observing/modeling the
market and deciding how to trade it:

    measurements -> market state -> regime posterior -> lifecycle forecast

Nothing in this module is allowed to describe an option candidate, a trading
permission, a selected action, execution, P&L, or position management.  The
same frozen observations and model artifacts must therefore produce the same
objects regardless of any later trade outcome.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from spy_der.contracts.common import (
    SCHEMA_VERSION,
    ValidationError,
    ErrorCode,
    deterministic_id,
    require_finite,
    require_non_negative,
    require_probability,
)

MARKET_STATE_VERSION = "alpha-v2-market-state.v1"
REGIME_MODEL_VERSION = "alpha-v2-regime-posterior.v1"
LIFECYCLE_FORECAST_VERSION = "alpha-v2-regime-lifecycle.v1"

CANONICAL_STATE_AXES: tuple[str, ...] = (
    "trend",
    "breadth_participation",
    "volatility_pressure",
    "dispersion_correlation",
    "liquidity_flow",
    "auction_location",
    "options_dealer",
    "cross_asset_risk",
    "positioning_actor",
    "cross_horizon_agreement",
    "transition_pressure",
)

PROHIBITED_MODEL_FIELD_TOKENS: tuple[str, ...] = (
    "candidate",
    "strategy",
    "trade",
    "order",
    "fill",
    "position",
    "pnl",
    "profit",
    "loss",
    "permission",
    "selected_action",
)

__all__ = [
    "CANONICAL_STATE_AXES",
    "LIFECYCLE_FORECAST_VERSION",
    "MARKET_STATE_VERSION",
    "PROHIBITED_MODEL_FIELD_TOKENS",
    "REGIME_MODEL_VERSION",
    "MarketState",
    "MarketStateAxis",
    "RegimeLifecycleForecast",
    "RegimePosterior",
    "RegimeProbability",
]


def _require_nonempty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValidationError(
            ErrorCode.MISSING_REQUIRED_INPUT,
            f"'{field_name}' is required",
        )


def _validate_probability_vector(
    values: tuple[RegimeProbability, ...],
    *,
    field_name: str,
) -> None:
    if not values:
        return
    labels = [item.label for item in values]
    if len(labels) != len(set(labels)):
        raise ValidationError(
            ErrorCode.MALFORMED_RECORD,
            f"'{field_name}' contains duplicate regime labels",
        )
    total = sum(item.probability for item in values)
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValidationError(
            ErrorCode.MALFORMED_RECORD,
            f"'{field_name}' probabilities must sum to 1, got {total!r}",
        )


@dataclass(frozen=True, slots=True)
class MarketStateAxis:
    """One standardized continuous description of current market state.

    ``value`` is intentionally not bounded to [0, 1].  State axes may be robust
    z-scores or other stable normalized quantities.  ``confidence`` describes
    measurement support and is a probability-like [0, 1] value.
    """

    name: str
    value: float | None
    confidence: float | None = None
    support: int | None = None
    source_measurements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty(self.name, "MarketStateAxis.name")
        if self.value is not None:
            require_finite(self.value, f"MarketStateAxis[{self.name}].value")
        if self.confidence is not None:
            require_probability(self.confidence, f"MarketStateAxis[{self.name}].confidence")
        if self.support is not None and self.support < 0:
            raise ValidationError(
                ErrorCode.NEGATIVE_VALUE,
                f"MarketStateAxis[{self.name}].support must be non-negative",
            )


@dataclass(frozen=True, slots=True)
class MarketState:
    """Compact, trade-independent representation of what the market is now."""

    snapshot_id: str
    measurement_bundle_id: str
    ts: str
    axes: tuple[MarketStateAxis, ...]
    state_version: str = MARKET_STATE_VERSION
    schema_version: str = SCHEMA_VERSION
    data_quality: float | None = None
    missing_measurements: tuple[str, ...] = ()
    state_id: str = ""

    def __post_init__(self) -> None:
        _require_nonempty(self.snapshot_id, "MarketState.snapshot_id")
        _require_nonempty(self.measurement_bundle_id, "MarketState.measurement_bundle_id")
        _require_nonempty(self.ts, "MarketState.ts")
        names = [axis.name for axis in self.axes]
        if len(names) != len(set(names)):
            raise ValidationError(
                ErrorCode.MALFORMED_RECORD,
                "MarketState.axes contains duplicate names",
            )
        if self.data_quality is not None:
            require_probability(self.data_quality, "MarketState.data_quality")
        if not self.state_id:
            object.__setattr__(
                self,
                "state_id",
                deterministic_id(
                    "mstate",
                    self.snapshot_id,
                    self.measurement_bundle_id,
                    self.ts,
                    self.state_version,
                    self.axes,
                    self.data_quality,
                    self.missing_measurements,
                ),
            )

    def axis(self, name: str) -> float | None:
        """Return a named state-axis value without manufacturing missing data."""
        for axis in self.axes:
            if axis.name == name:
                return axis.value
        return None


@dataclass(frozen=True, slots=True)
class RegimeProbability:
    """Probability assigned to one latent market regime."""

    label: str
    probability: float

    def __post_init__(self) -> None:
        _require_nonempty(self.label, "RegimeProbability.label")
        require_probability(self.probability, f"RegimeProbability[{self.label}]")


@dataclass(frozen=True, slots=True)
class RegimePosterior:
    """Full posterior over the current latent regime.

    Consumers should use the probability vector rather than treating the
    dominant label as certain.  The dominant label remains a diagnostic view.
    """

    market_state_id: str
    probabilities: tuple[RegimeProbability, ...]
    model_version: str = REGIME_MODEL_VERSION
    schema_version: str = SCHEMA_VERSION
    current_age_minutes: float | None = None
    posterior_id: str = ""

    def __post_init__(self) -> None:
        _require_nonempty(self.market_state_id, "RegimePosterior.market_state_id")
        _validate_probability_vector(self.probabilities, field_name="RegimePosterior.probabilities")
        if self.current_age_minutes is not None:
            require_non_negative(self.current_age_minutes, "RegimePosterior.current_age_minutes")
        if not self.posterior_id:
            object.__setattr__(
                self,
                "posterior_id",
                deterministic_id(
                    "regime",
                    self.market_state_id,
                    self.model_version,
                    self.probabilities,
                    self.current_age_minutes,
                ),
            )

    @property
    def dominant_regime(self) -> str | None:
        if not self.probabilities:
            return None
        return max(self.probabilities, key=lambda item: item.probability).label

    @property
    def dominant_probability(self) -> float | None:
        if not self.probabilities:
            return None
        return max(item.probability for item in self.probabilities)

    @property
    def normalized_entropy(self) -> float | None:
        """Posterior entropy normalized to [0, 1]; higher means less certain."""
        n = len(self.probabilities)
        if n <= 1:
            return 0.0 if n == 1 else None
        entropy = -sum(
            item.probability * math.log(item.probability)
            for item in self.probabilities
            if item.probability > 0.0
        )
        return entropy / math.log(n)


@dataclass(frozen=True, slots=True)
class RegimeLifecycleForecast:
    """Forecast of persistence, transition destination, and transition timing."""

    regime_posterior_id: str
    current_regime: str
    survival_probabilities: tuple[tuple[int, float], ...]
    next_regime_probabilities: tuple[RegimeProbability, ...] = ()
    conditional_p_up_if_transition: float | None = None
    expected_remaining_minutes: float | None = None
    median_transition_minutes: float | None = None
    transition_q25_minutes: float | None = None
    transition_q75_minutes: float | None = None
    model_version: str = LIFECYCLE_FORECAST_VERSION
    calibration_version: str = ""
    transition_model_role: str = "advisory"
    schema_version: str = SCHEMA_VERSION
    lifecycle_forecast_id: str = ""

    def __post_init__(self) -> None:
        _require_nonempty(self.regime_posterior_id, "RegimeLifecycleForecast.regime_posterior_id")
        _require_nonempty(self.current_regime, "RegimeLifecycleForecast.current_regime")
        self._validate_survival_curve()
        _validate_probability_vector(
            self.next_regime_probabilities,
            field_name="RegimeLifecycleForecast.next_regime_probabilities",
        )
        if any(item.label == self.current_regime for item in self.next_regime_probabilities):
            raise ValidationError(
                ErrorCode.MALFORMED_RECORD,
                "next-regime distribution must describe a transition away from current_regime",
            )
        if self.conditional_p_up_if_transition is not None:
            require_probability(
                self.conditional_p_up_if_transition,
                "RegimeLifecycleForecast.conditional_p_up_if_transition",
            )
        for field_name in (
            "expected_remaining_minutes",
            "median_transition_minutes",
            "transition_q25_minutes",
            "transition_q75_minutes",
        ):
            value = getattr(self, field_name)
            if value is not None:
                require_non_negative(value, f"RegimeLifecycleForecast.{field_name}")
        if (
            self.transition_q25_minutes is not None
            and self.median_transition_minutes is not None
            and self.transition_q25_minutes > self.median_transition_minutes
        ):
            raise ValidationError(
                ErrorCode.MALFORMED_RECORD,
                "transition_q25_minutes cannot exceed median_transition_minutes",
            )
        if (
            self.transition_q75_minutes is not None
            and self.median_transition_minutes is not None
            and self.transition_q75_minutes < self.median_transition_minutes
        ):
            raise ValidationError(
                ErrorCode.MALFORMED_RECORD,
                "transition_q75_minutes cannot be below median_transition_minutes",
            )
        if not self.lifecycle_forecast_id:
            object.__setattr__(
                self,
                "lifecycle_forecast_id",
                deterministic_id(
                    "life",
                    self.regime_posterior_id,
                    self.current_regime,
                    self.survival_probabilities,
                    self.next_regime_probabilities,
                    self.conditional_p_up_if_transition,
                    self.expected_remaining_minutes,
                    self.median_transition_minutes,
                    self.transition_q25_minutes,
                    self.transition_q75_minutes,
                    self.model_version,
                    self.calibration_version,
                ),
            )

    def _validate_survival_curve(self) -> None:
        previous_horizon = 0
        previous_probability = 1.0
        for horizon_minutes, probability in self.survival_probabilities:
            if horizon_minutes <= previous_horizon:
                raise ValidationError(
                    ErrorCode.MALFORMED_RECORD,
                    "survival horizons must be positive and strictly increasing",
                )
            require_probability(
                probability,
                f"RegimeLifecycleForecast.survival[{horizon_minutes}m]",
            )
            if probability > previous_probability + 1e-12:
                raise ValidationError(
                    ErrorCode.MALFORMED_RECORD,
                    "regime survival probability cannot increase with horizon",
                )
            previous_horizon = horizon_minutes
            previous_probability = probability

    def survival_probability(self, horizon_minutes: int) -> float | None:
        """Return the exact requested persistence forecast, if it was emitted."""
        for horizon, probability in self.survival_probabilities:
            if horizon == horizon_minutes:
                return probability
        return None
