"""Executable economics (master spec §33, §34)."""

from spy_der.economics.fill_prior import FillPriorConfig, fill_fraction_for
from spy_der.economics.fill_training import fallback_level, stage1_attempts, stage2_fills
from spy_der.economics.models import (
    FillConcessionForecast,
    FillConcessionModel,
    FillProbabilityForecast,
    FillProbabilityModel,
)
from spy_der.economics.service import (
    EconomicsConfig,
    calculate_candidate_economics,
    calculate_universe_economics,
    expected_order_value,
)

__all__ = [
    "EconomicsConfig",
    "FillConcessionForecast",
    "FillConcessionModel",
    "FillPriorConfig",
    "FillProbabilityForecast",
    "FillProbabilityModel",
    "calculate_candidate_economics",
    "calculate_universe_economics",
    "expected_order_value",
    "fallback_level",
    "fill_fraction_for",
    "stage1_attempts",
    "stage2_fills",
]
