"""Trade-independent Observation Engine contracts for Alpha V2.

The Observation Engine converts point-in-time market feeds into stable,
versioned measurements.  Missing values remain explicit.  Normalization state
is versioned so a historical replay cannot accidentally use future statistics.
"""

from __future__ import annotations

from dataclasses import dataclass

from spy_der.contracts.common import (
    SCHEMA_VERSION,
    ErrorCode,
    ValidationError,
    deterministic_id,
    require_finite,
    require_probability,
)

MEASUREMENT_BUNDLE_VERSION = "alpha-v2-observation.v1"

__all__ = [
    "MEASUREMENT_BUNDLE_VERSION",
    "MeasurementBundle",
    "MeasurementValue",
]


def _required(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValidationError(
            ErrorCode.MISSING_REQUIRED_INPUT,
            f"'{field_name}' is required",
        )


@dataclass(frozen=True, slots=True)
class MeasurementValue:
    """One canonical standardized measurement available at a decision timestamp."""

    variable_id: str
    name: str
    value: float | None
    unit: str = ""
    horizon: str = ""
    normalization: str = ""
    quality: float | None = None
    source_ids: tuple[str, ...] = ()
    missing_reason: str | None = None

    def __post_init__(self) -> None:
        _required(self.variable_id, "MeasurementValue.variable_id")
        _required(self.name, "MeasurementValue.name")
        if self.value is None:
            if not self.missing_reason:
                raise ValidationError(
                    ErrorCode.MISSING_REQUIRED_INPUT,
                    f"MeasurementValue[{self.variable_id}] missing value requires missing_reason",
                )
        else:
            require_finite(self.value, f"MeasurementValue[{self.variable_id}].value")
            if self.missing_reason is not None:
                raise ValidationError(
                    ErrorCode.MALFORMED_RECORD,
                    f"MeasurementValue[{self.variable_id}] cannot have value and missing_reason",
                )
        if self.quality is not None:
            require_probability(self.quality, f"MeasurementValue[{self.variable_id}].quality")


@dataclass(frozen=True, slots=True)
class MeasurementBundle:
    """Immutable standardized sensor vector for one canonical market snapshot."""

    snapshot_id: str
    ts: str
    values: tuple[MeasurementValue, ...]
    dictionary_version: str
    normalization_state_version: str
    measurement_version: str = MEASUREMENT_BUNDLE_VERSION
    schema_version: str = SCHEMA_VERSION
    data_quality: float | None = None
    feature_coverage: float | None = None
    bundle_id: str = ""

    def __post_init__(self) -> None:
        _required(self.snapshot_id, "MeasurementBundle.snapshot_id")
        _required(self.ts, "MeasurementBundle.ts")
        _required(self.dictionary_version, "MeasurementBundle.dictionary_version")
        _required(
            self.normalization_state_version,
            "MeasurementBundle.normalization_state_version",
        )
        variable_ids = [item.variable_id for item in self.values]
        if len(variable_ids) != len(set(variable_ids)):
            raise ValidationError(
                ErrorCode.MALFORMED_RECORD,
                "MeasurementBundle.values contains duplicate variable_ids",
            )
        if self.data_quality is not None:
            require_probability(self.data_quality, "MeasurementBundle.data_quality")
        if self.feature_coverage is not None:
            require_probability(self.feature_coverage, "MeasurementBundle.feature_coverage")
        if not self.bundle_id:
            object.__setattr__(
                self,
                "bundle_id",
                deterministic_id(
                    "measure",
                    self.snapshot_id,
                    self.ts,
                    self.dictionary_version,
                    self.normalization_state_version,
                    self.measurement_version,
                    self.values,
                    self.data_quality,
                    self.feature_coverage,
                ),
            )

    @property
    def missing_variable_ids(self) -> tuple[str, ...]:
        return tuple(item.variable_id for item in self.values if item.value is None)

    def get(self, variable_id: str) -> float | None:
        """Return a measurement by canonical variable id; missing stays missing."""
        for item in self.values:
            if item.variable_id == variable_id:
                return item.value
        return None
