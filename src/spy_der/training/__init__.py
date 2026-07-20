"""Training substrate: as-of datasets, folds, calibration, and registry."""

from __future__ import annotations

from spy_der.training.asof import AsOfFeatureBuilder, AsOfViolation, bars_asof, ensure_asof
from spy_der.training.calibration import (
    CalibrationArtifact,
    IdentityCalibrator,
    SigmoidCalibrator,
    fit_calibrator,
)
from spy_der.training.datasets import (
    FEATURE_VERSION,
    LABEL_VERSION,
    ObservationRow,
    build_observation,
    make_snapshot_id,
    normalize_ts,
    session_metadata,
)
from spy_der.training.folds import (
    FoldDefinition,
    NestedCrossFitConfig,
    build_expanding_session_folds,
    build_nested_session_folds,
)
from spy_der.training.registry import ModelGroupMetadata, ModelRegistry, RegistryError

__all__ = [
    "FEATURE_VERSION",
    "LABEL_VERSION",
    "AsOfFeatureBuilder",
    "AsOfViolation",
    "CalibrationArtifact",
    "FoldDefinition",
    "IdentityCalibrator",
    "ModelGroupMetadata",
    "ModelRegistry",
    "NestedCrossFitConfig",
    "ObservationRow",
    "RegistryError",
    "SigmoidCalibrator",
    "bars_asof",
    "build_expanding_session_folds",
    "build_nested_session_folds",
    "build_observation",
    "ensure_asof",
    "fit_calibrator",
    "make_snapshot_id",
    "normalize_ts",
    "session_metadata",
]
