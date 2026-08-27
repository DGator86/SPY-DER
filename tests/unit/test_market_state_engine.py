from __future__ import annotations

from spy_der.contracts.observations import MeasurementBundle, MeasurementValue
from spy_der.market_model.state import AxisDefinition, BaselineStateEngine


def _measurements() -> MeasurementBundle:
    return MeasurementBundle(
        snapshot_id="snap-1",
        ts="2026-08-27T14:30:00-04:00",
        values=(
            MeasurementValue(
                variable_id="TREND_FAST",
                name="trend_fast",
                value=1.0,
                normalization="robust_z",
                quality=1.0,
            ),
            MeasurementValue(
                variable_id="TREND_SLOW",
                name="trend_slow",
                value=0.5,
                normalization="robust_z",
                quality=0.8,
            ),
            MeasurementValue(
                variable_id="BREADTH",
                name="breadth",
                value=None,
                normalization="robust_z",
                quality=0.5,
                missing_reason="source_stale",
            ),
        ),
        dictionary_version="utpm-test",
        normalization_state_version="fold-7",
        data_quality=0.9,
        feature_coverage=2 / 3,
    )


def test_baseline_state_engine_is_deterministic_and_explicit_about_missingness() -> None:
    engine = BaselineStateEngine(
        definitions=(
            AxisDefinition(
                name="trend",
                variable_ids=("TREND_FAST", "TREND_SLOW"),
                weights=(0.5, 0.5),
            ),
            AxisDefinition(
                name="breadth_participation",
                variable_ids=("BREADTH",),
                weights=(1.0,),
            ),
        )
    )
    first = engine.build(_measurements())
    second = engine.build(_measurements())
    assert first.state_id == second.state_id
    assert first.axis("trend") == 0.75
    assert first.axis("breadth_participation") is None
    breadth_axis = next(axis for axis in first.axes if axis.name == "breadth_participation")
    assert breadth_axis.confidence == 0.0
    assert breadth_axis.support == 0
    assert "BREADTH" in first.missing_measurements


def test_partial_axis_support_reduces_confidence_without_imputation() -> None:
    engine = BaselineStateEngine(
        definitions=(
            AxisDefinition(
                name="participation",
                variable_ids=("TREND_FAST", "BREADTH"),
                weights=(0.5, 0.5),
            ),
        )
    )
    state = engine.build(_measurements())
    axis = state.axes[0]
    assert axis.value == 1.0
    assert axis.confidence == 0.5
    assert axis.support == 1
    assert axis.source_measurements == ("TREND_FAST",)
