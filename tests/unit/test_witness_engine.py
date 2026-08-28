from __future__ import annotations

from spy_der.contracts.forecasts import MarketForecastBundle
from spy_der.runtime.witness_engine import WitnessEngineService


def _forecast() -> MarketForecastBundle:
    return MarketForecastBundle(
        snapshot_id="snap-1",
        ts="2026-08-27T18:00:00+00:00",
        session_date="2026-08-27",
        model_group_id="alpha-v2-test",
        p_up_15m=0.55,
        p_up_30m=0.58,
        expected_return_15m=0.0002,
        expected_return_30m=0.0004,
    )


def _beta_record() -> dict:
    return {
        "market_snapshot_id": "snap-1",
        "available": True,
        "unavailable_reason": "",
        "witness": {
            "source_timestamp": "2026-08-27T17:59:59+00:00",
            "source_version": "beta-test",
            "coverage_ratio": 0.98,
            "covered_weight": 0.97,
            "horizons": [
                {
                    "horizon_minutes": 15,
                    "probability_up": 0.63,
                    "expected_return": 0.0005,
                    "confidence": 0.72,
                    "model_ready": True,
                    "sample_count": 900,
                },
                {
                    "horizon_minutes": 30,
                    "probability_up": 0.48,
                    "expected_return": -0.0003,
                    "confidence": 0.68,
                    "model_ready": True,
                    "sample_count": 700,
                },
            ],
        },
    }


def test_beta_shadow_is_recorded_but_cannot_change_alpha_physical_forecast() -> None:
    base = _forecast()
    enriched = WitnessEngineService._attach_beta_shadow(base, _beta_record())

    assert enriched.forecast_id == base.forecast_id
    assert enriched.p_up_15m == base.p_up_15m
    assert enriched.p_up_30m == base.p_up_30m
    assert enriched.expected_return_15m == base.expected_return_15m
    assert enriched.expected_return_30m == base.expected_return_30m
    assert enriched.content_hash != base.content_hash

    shadow = enriched.diagnostics["beta_witness"]
    assert shadow["available"] is True
    assert shadow["trading_authority"] is False
    assert shadow["blend_weight"] == 0.0
    assert shadow["horizons"]["15m"]["probability_up"] == 0.63
    assert shadow["horizons"]["15m"]["probability_disagreement"] == 0.08


def test_missing_beta_is_explicit_and_never_defaults_to_neutral() -> None:
    enriched = WitnessEngineService._attach_beta_shadow(_forecast(), None)
    shadow = enriched.diagnostics["beta_witness"]
    assert shadow == {
        "available": False,
        "reason": "no_frozen_beta_record",
        "trading_authority": False,
    }
