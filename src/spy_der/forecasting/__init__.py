"""V2/V3 forecasting runtime and models (master spec §24, §26-§29)."""

from __future__ import annotations

from typing import Any

from spy_der.forecasting.conformal import ConformalInterval, SplitConformalCalibrator
from spy_der.forecasting.ensemble import EnsembleForecast, ForecastEnsemble
from spy_der.forecasting.models import (
    CompetingRiskForecast,
    CompetingRiskModel,
    DirectionModel,
    FeatureVectorizer,
    MixtureOfExperts,
    RangeSurvivalModel,
    RegimeProbabilities,
    RegimeProbabilityModel,
    ReturnQuantileModel,
    VolatilityModel,
)
from spy_der.forecasting.ood import OODDetector, OODResult
from spy_der.forecasting.path_model import PathForecastV3, derive_path_seed, simulate_paths_v3
from spy_der.forecasting.regime_labels import REGIME_CLASSES
from spy_der.forecasting.runtime import ForecastServer, ForecastServingError, heuristic_bundle
from spy_der.forecasting.uncertainty import UncertaintyComponents, compose_uncertainty
from spy_der.forecasting.v3 import attach_v3_fields

__all__ = [
    "REGIME_CLASSES",
    "BarrierTouchModel",
    "CompetingRiskForecast",
    "CompetingRiskModel",
    "ConformalInterval",
    "DirectionModel",
    "EnsembleForecast",
    "FeatureVectorizer",
    "ForecastEnsemble",
    "ForecastServer",
    "ForecastServingError",
    "MixtureOfExperts",
    "OODDetector",
    "OODResult",
    "PathForecastV3",
    "RangeSurvivalModel",
    "RegimeProbabilities",
    "RegimeProbabilityModel",
    "ReturnQuantileModel",
    "SplitConformalCalibrator",
    "UncertaintyComponents",
    "VolatilityModel",
    "attach_v3_fields",
    "compose_uncertainty",
    "derive_path_seed",
    "heuristic_bundle",
    "simulate_paths_v3",
]


def __getattr__(name: str) -> Any:
    if name == "BarrierTouchModel":
        from spy_der.forecasting.models.barrier_touch import BarrierTouchModel

        return BarrierTouchModel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
