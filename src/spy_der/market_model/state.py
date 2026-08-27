"""Transparent baseline construction of Alpha V2 market-state axes.

This is deliberately a benchmark implementation, not a claim that weighted
linear composites are the final state model. It creates a deterministic,
inspectable baseline against which learned latent representations can be
ablated. Missing measurements are never imputed silently.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from spy_der.contracts.common import ErrorCode, ValidationError
from spy_der.contracts.market_model import MarketState, MarketStateAxis
from spy_der.contracts.observations import MeasurementBundle

BASELINE_STATE_ENGINE_VERSION = "alpha-v2-baseline-state.v1"

__all__ = ["BASELINE_STATE_ENGINE_VERSION", "AxisDefinition", "BaselineStateEngine"]


@dataclass(frozen=True, slots=True)
class AxisDefinition:
    """Versioned transparent mapping from standardized measurements to one axis."""

    name: str
    variable_ids: tuple[str, ...]
    weights: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValidationError(ErrorCode.MISSING_REQUIRED_INPUT, "axis name is required")
        if not self.variable_ids:
            raise ValidationError(
                ErrorCode.MISSING_REQUIRED_INPUT,
                f"axis {self.name!r} requires at least one variable",
            )
        if len(self.variable_ids) != len(self.weights):
            raise ValidationError(
                ErrorCode.MALFORMED_RECORD,
                f"axis {self.name!r} variable_ids and weights must have equal length",
            )
        if len(set(self.variable_ids)) != len(self.variable_ids):
            raise ValidationError(
                ErrorCode.MALFORMED_RECORD,
                f"axis {self.name!r} contains duplicate variable_ids",
            )
        if not all(math.isfinite(weight) for weight in self.weights):
            raise ValidationError(
                ErrorCode.NON_FINITE_NUMBER,
                f"axis {self.name!r} contains a non-finite weight",
            )
        if sum(abs(weight) for weight in self.weights) <= 0.0:
            raise ValidationError(
                ErrorCode.MALFORMED_RECORD,
                f"axis {self.name!r} requires non-zero total absolute weight",
            )


@dataclass(frozen=True, slots=True)
class BaselineStateEngine:
    """Build deterministic continuous state axes from standardized measurements."""

    definitions: tuple[AxisDefinition, ...]
    state_version: str = BASELINE_STATE_ENGINE_VERSION

    def __post_init__(self) -> None:
        names = [definition.name for definition in self.definitions]
        if len(names) != len(set(names)):
            raise ValidationError(
                ErrorCode.MALFORMED_RECORD,
                "state engine contains duplicate axis names",
            )

    def build(self, measurements: MeasurementBundle) -> MarketState:
        axes = tuple(self._axis(definition, measurements) for definition in self.definitions)
        referenced = {
            variable_id
            for definition in self.definitions
            for variable_id in definition.variable_ids
        }
        available_ids = {item.variable_id for item in measurements.values}
        absent_ids = referenced - available_ids
        missing = tuple(sorted(set(measurements.missing_variable_ids) | absent_ids))
        return MarketState(
            snapshot_id=measurements.snapshot_id,
            measurement_bundle_id=measurements.bundle_id,
            ts=measurements.ts,
            axes=axes,
            state_version=self.state_version,
            data_quality=measurements.data_quality,
            missing_measurements=missing,
        )

    @staticmethod
    def _axis(
        definition: AxisDefinition,
        measurements: MeasurementBundle,
    ) -> MarketStateAxis:
        by_id = {item.variable_id: item for item in measurements.values}
        used: list[tuple[float, float, float]] = []
        total_abs_weight = sum(abs(weight) for weight in definition.weights)

        for variable_id, weight in zip(
            definition.variable_ids,
            definition.weights,
            strict=True,
        ):
            measurement = by_id.get(variable_id)
            if measurement is None or measurement.value is None:
                continue
            quality = 1.0 if measurement.quality is None else measurement.quality
            used.append((measurement.value, weight, quality))

        if not used:
            return MarketStateAxis(
                name=definition.name,
                value=None,
                confidence=0.0,
                support=0,
                source_measurements=(),
            )

        used_abs_weight = sum(abs(weight) for _, weight, _ in used)
        signed_weight_scale = sum(abs(weight) for _, weight, _ in used)
        value = sum(value * weight for value, weight, _ in used) / signed_weight_scale
        coverage = used_abs_weight / total_abs_weight
        quality = (
            sum(abs(weight) * item_quality for _, weight, item_quality in used)
            / used_abs_weight
        )
        confidence = max(0.0, min(1.0, coverage * quality))
        used_ids = tuple(
            variable_id
            for variable_id in definition.variable_ids
            if (measurement := by_id.get(variable_id)) is not None
            and measurement.value is not None
        )
        return MarketStateAxis(
            name=definition.name,
            value=value,
            confidence=confidence,
            support=len(used),
            source_measurements=used_ids,
        )
