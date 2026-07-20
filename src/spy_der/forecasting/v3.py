"""Attach Phase 6 / V3 forecast extensions onto MarketForecastBundle."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from spy_der.contracts.forecasts import MarketForecastBundle
from spy_der.forecasting.ensemble import EnsembleForecast
from spy_der.forecasting.models.competing_risk import CompetingRiskForecast
from spy_der.forecasting.models.mixture_experts import MixtureForecast
from spy_der.forecasting.models.regime_moe import RegimeProbabilities
from spy_der.forecasting.ood import OODResult
from spy_der.forecasting.path_model import PathForecastV3
from spy_der.forecasting.uncertainty import UncertaintyComponents

__all__ = ["attach_v3_fields"]


def attach_v3_fields(
    bundle: MarketForecastBundle,
    *,
    uncertainty: UncertaintyComponents | None = None,
    ood: OODResult | None = None,
    regime: RegimeProbabilities | None = None,
    competing_risk: Mapping[str, CompetingRiskForecast] | None = None,
    paths: Mapping[str, PathForecastV3] | None = None,
    ensembles: Mapping[str, EnsembleForecast] | None = None,
    mixtures: Mapping[str, MixtureForecast] | None = None,
    return_distributions: Mapping[str, Any] | None = None,
    structural_state_version: str | None = None,
) -> MarketForecastBundle:
    """Return a new bundle with V3 fields populated (frozen → rebuild)."""
    data = bundle.to_dict()
    # Clear derived identity so __post_init__ recomputes for the new payload.
    data["forecast_id"] = ""
    data["content_hash"] = ""

    if uncertainty is not None:
        data["uncertainty"] = uncertainty.composite
        data["uncertainty_components"] = {
            "ensemble": uncertainty.ensemble,
            "conformal": uncertainty.conformal,
            "out_of_distribution": uncertainty.out_of_distribution,
            "calibration": uncertainty.calibration,
            "data_quality": uncertainty.data_quality,
            "model_age": uncertainty.model_age,
            "composite": uncertainty.composite,
        }
        data["uncertainty_reasons"] = uncertainty.reasons
        if uncertainty.calibration is not None:
            data["calibration_support"] = float(max(0.0, 1.0 - uncertainty.calibration))

    if ood is not None:
        data["ood_score"] = ood.score
        data["ood_percentile"] = ood.percentile
        diagnostics = dict(data.get("diagnostics") or {})
        diagnostics["ood_state_bucket"] = ood.state_bucket
        diagnostics["ood_reasons"] = list(ood.reasons)
        data["diagnostics"] = diagnostics

    if regime is not None:
        data["regime_probabilities"] = regime.as_dict()
        data["regime_uncertainty"] = regime.uncertainty
        data["dominant_regime"] = regime.dominant_regime

    if competing_risk is not None:
        data["competing_risk_forecasts"] = {k: v.to_dict() for k, v in competing_risk.items()}

    if paths is not None:
        data["path_forecasts"] = {k: v.to_dict() for k, v in paths.items()}

    if ensembles is not None:
        data["ensemble_forecasts"] = {k: v.to_dict() for k, v in ensembles.items()}
        data["ensemble_size"] = max(
            (len(v.component_predictions) for v in ensembles.values()),
            default=None,
        )

    if mixtures is not None:
        diagnostics = dict(data.get("diagnostics") or {})
        diagnostics["mixture_forecasts"] = {k: v.to_dict() for k, v in mixtures.items()}
        data["diagnostics"] = diagnostics

    if return_distributions is not None:
        data["return_distributions"] = dict(return_distributions)

    if structural_state_version is not None:
        data["structural_state_version"] = structural_state_version

    return MarketForecastBundle.from_dict(data)
