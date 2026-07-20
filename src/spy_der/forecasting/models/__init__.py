"""V2/V3 forecasting model suite (master spec §24, §27, §28)."""

from __future__ import annotations

from typing import Any

from spy_der.forecasting.models.base import RANDOM_STATE, FeatureVectorizer
from spy_der.forecasting.models.competing_risk import CompetingRiskForecast, CompetingRiskModel
from spy_der.forecasting.models.direction import DirectionModel
from spy_der.forecasting.models.mixture_experts import MixtureForecast, MixtureOfExperts
from spy_der.forecasting.models.range_survival import RangeSurvivalModel
from spy_der.forecasting.models.regime_moe import RegimeProbabilities, RegimeProbabilityModel
from spy_der.forecasting.models.return_quantiles import ReturnQuantileModel
from spy_der.forecasting.models.volatility import VolatilityModel

__all__ = [
    "RANDOM_STATE",
    "BarrierTouchModel",
    "CompetingRiskForecast",
    "CompetingRiskModel",
    "DirectionModel",
    "FeatureVectorizer",
    "MixtureForecast",
    "MixtureOfExperts",
    "RangeSurvivalModel",
    "RegimeProbabilities",
    "RegimeProbabilityModel",
    "ReturnQuantileModel",
    "VolatilityModel",
]


def __getattr__(name: str) -> Any:
    # Lazy import breaks calibration <-> barrier_touch circular dependency.
    if name == "BarrierTouchModel":
        from spy_der.forecasting.models.barrier_touch import BarrierTouchModel

        return BarrierTouchModel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
