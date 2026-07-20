"""Phase 5 unit tests: as-of, labels, folds, calibration, registry, serving."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from spy_der.contracts import MarketForecastBundle, MissingInputError
from spy_der.contracts.market import Bar
from spy_der.evaluation.labels import SessionLabeler, direction_label, first_passage
from spy_der.forecasting.models.direction import DirectionModel, DirectionModelConfig
from spy_der.forecasting.runtime import ForecastServer, ForecastServingError, heuristic_bundle
from spy_der.training.asof import AsOfFeatureBuilder, AsOfViolation, bars_asof, ensure_asof
from spy_der.training.calibration import build_calibration_artifact, fit_calibrator
from spy_der.training.datasets import build_observation, make_snapshot_id
from spy_der.training.folds import NestedCrossFitConfig, build_nested_session_folds
from spy_der.training.registry import ModelRegistry, RegistryError

ET = ZoneInfo("America/New_York")


def test_asof_rejects_future_source() -> None:
    obs = datetime(2026, 1, 5, 10, 0, tzinfo=ET)
    future = obs + timedelta(seconds=1)
    with pytest.raises(AsOfViolation):
        ensure_asof("spot", future, obs)


def test_asof_builder_records_missingness() -> None:
    obs = datetime(2026, 1, 5, 10, 0, tzinfo=ET)
    builder = AsOfFeatureBuilder(observation_ts=obs)
    builder.add("spot", 500.0, source_ts=obs)
    builder.add_missing("net_gex")
    payload = builder.build()
    assert payload["features"]["spot"] == 500.0
    assert payload["missingness"]["net_gex"] == 1
    assert payload["coverage"] == 0.5


def test_bars_asof_filters_future_bars() -> None:
    t0 = datetime(2026, 1, 5, 10, 0, tzinfo=ET)
    bars = (
        Bar(t0, Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"), 1),
        Bar(t0 + timedelta(minutes=1), Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"), 1),
    )
    kept = bars_asof(bars, t0)
    assert len(kept) == 1
    assert kept[0].timestamp == t0


def test_observation_snapshot_id_is_stable() -> None:
    ts = datetime(2026, 1, 5, 10, 30, tzinfo=ET)
    a = make_snapshot_id("SPY", ts, "v2.0.0", 0)
    b = make_snapshot_id("SPY", ts, "v2.0.0", 0)
    assert a == b
    row = build_observation("SPY", ts, 500.0, features={"spot": 500.0, "rsi": None})
    assert row.snapshot_id == a
    assert row.session_date == "2026-01-05"


def test_direction_and_first_passage_labels() -> None:
    assert direction_label(0.01) == 1
    assert direction_label(-0.01) == -1
    assert direction_label(0.0) == 0
    fp = first_passage([101, 102], [99, 100], [1.0, 2.0], target=101.5, stop=98.0)
    assert fp["first_event"] == "target"


def test_session_labeler_horizon_past_close_is_none() -> None:
    start = datetime(2026, 1, 5, 15, 50, tzinfo=ET)
    ts = np.asarray(
        [np.datetime64("2026-01-05T20:50:00"), np.datetime64("2026-01-05T20:51:00")],
        dtype="datetime64[ns]",
    )
    labeler = SessionLabeler(
        ts=ts,
        high=np.array([501.0, 502.0]),
        low=np.array([499.0, 500.0]),
        close=np.array([500.5, 501.0]),
    )
    labels = labeler.label_observation(start, spot=500.0, call_wall=510.0, put_wall=490.0)
    assert labels["fwd_return_60m"] is None
    assert labels["fwd_return_close"] is not None


def test_nested_folds_embargo_and_no_overlap() -> None:
    sessions = [f"2026-01-{i:02d}" for i in range(3, 24)]
    folds = build_nested_session_folds(
        sessions,
        NestedCrossFitConfig(
            outer_folds=3,
            embargo_sessions=1,
            min_train_sessions=8,
            min_validation_sessions=2,
        ),
    )
    assert folds
    for fold in folds:
        train = set(fold.train_sessions)
        val = set(fold.validation_sessions)
        cal = set(fold.calibration_sessions)
        embargo = set(fold.embargoed_sessions)
        assert train.isdisjoint(val)
        assert train.isdisjoint(embargo)
        assert cal.isdisjoint(val)


def test_sigmoid_calibrator_and_artifact() -> None:
    rng = np.random.default_rng(7)
    p = rng.uniform(0.1, 0.9, size=200)
    y = (p + rng.normal(0, 0.1, size=200) > 0.5).astype(int)
    cal = fit_calibrator(p, y, method="sigmoid")
    out = cal.transform(p)
    assert out.shape == (200,)
    assert np.all((out >= 0.0) & (out <= 1.0))
    art = build_calibration_artifact(p, y, ["s"] * 200, method="sigmoid")
    assert art.method == "sigmoid"
    assert art.oof_n == 200


def test_direction_model_fit_predict() -> None:
    rows = [{"x": float(i), "y": float(i % 3)} for i in range(80)]
    y = [1 if i % 2 == 0 else 0 for i in range(80)]
    sessions = [f"s{i // 10:02d}" for i in range(80)]
    model = DirectionModel(DirectionModelConfig(horizon="30m", calibration_frac=0.25))
    model.fit(rows, y, sessions)
    proba = model.predict_proba(rows[:5])
    assert proba.shape == (5,)
    assert np.all((proba >= 0.0) & (proba <= 1.0))


def test_registry_fail_closed_on_hash_mismatch(tmp_path: object) -> None:
    registry = ModelRegistry(directory=str(tmp_path))
    model = {"ok": True}
    mid = registry.save(
        model,
        model_type="direction",
        target="up_30m",
        horizon="30m",
        feature_version="v2.0.0",
        label_version="v2.0.0",
        crossfit_config={"outer_folds": 2},
        fold_hash="abc",
        oof_metrics={"brier": 0.2},
        calibration_artifact={"method": "sigmoid"},
        uncertainty_method="none",
        training_feature_distribution_hash="def",
        required_input_fields=["spot"],
        dependency_versions={"sklearn": "1.9"},
        git_commit="de4a6e7",
        status="research",
    )
    artifact = registry._artifact_path(mid)
    with open(artifact, "ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(RegistryError, match="hash mismatch"):
        registry.load(mid, load_mode="research")


def test_registry_status_gates_serving_mode(tmp_path: object) -> None:
    registry = ModelRegistry(directory=str(tmp_path))
    mid = registry.save(
        {"m": 1},
        model_type="direction",
        target="up_30m",
        horizon="30m",
        feature_version="v2.0.0",
        label_version="v2.0.0",
        crossfit_config={},
        fold_hash="fold",
        oof_metrics={},
        calibration_artifact={"method": "identity"},
        uncertainty_method="none",
        training_feature_distribution_hash="h",
        required_input_fields=["spot"],
        dependency_versions={},
        git_commit="x",
        status="research",
    )
    with pytest.raises(RegistryError, match="does not allow load_mode"):
        registry.load(mid, load_mode="champion")


def test_forecast_server_fail_closed_missing_inputs(tmp_path: object) -> None:
    registry = ModelRegistry(directory=str(tmp_path))
    rows = [{"spot": float(100 + i), "x": float(i)} for i in range(60)]
    y = [1 if i % 2 == 0 else 0 for i in range(60)]
    sessions = [f"s{i // 10:02d}" for i in range(60)]
    model = DirectionModel(DirectionModelConfig(horizon="30m")).fit(rows, y, sessions)
    mid = registry.save(
        model,
        model_type="direction",
        target="up_30m",
        horizon="30m",
        feature_version="v2.0.0",
        label_version="v2.0.0",
        crossfit_config={"outer_folds": 2},
        fold_hash="fold",
        oof_metrics={"brier": 0.25},
        calibration_artifact=model.calibration_artifact or {"method": "sigmoid"},
        uncertainty_method="none",
        training_feature_distribution_hash="dist",
        required_input_fields=["spot", "x"],
        dependency_versions={"sklearn": "1.9"},
        git_commit="de4a6e7",
        status="shadow",
    )
    group = registry.save_group(
        component_model_ids={"direction_30m": mid},
        feature_version="v2.0.0",
        label_version="v2.0.0",
        status="shadow",
        group_id="phase5-test",
    )
    server = ForecastServer(
        registry=registry,
        group_id=group.group_id,
        load_mode="shadow",
        required_input_fields=("spot", "x"),
    ).load()
    with pytest.raises(MissingInputError):
        server.predict(
            snapshot_id="snap",
            ts="2026-01-05T10:30:00-05:00",
            session_date="2026-01-05",
            symbol="SPY",
            feature_row={"spot": 500.0},  # missing x
        )


def test_forecast_server_predicts_bundle(tmp_path: object) -> None:
    registry = ModelRegistry(directory=str(tmp_path))
    rows = [{"spot": float(100 + i), "x": float(i)} for i in range(60)]
    y = [1 if i % 2 == 0 else 0 for i in range(60)]
    sessions = [f"s{i // 10:02d}" for i in range(60)]
    model = DirectionModel(DirectionModelConfig(horizon="30m")).fit(rows, y, sessions)
    mid = registry.save(
        model,
        model_type="direction",
        target="up_30m",
        horizon="30m",
        feature_version="v2.0.0",
        label_version="v2.0.0",
        crossfit_config={"outer_folds": 2},
        fold_hash="fold",
        oof_metrics={"brier": 0.25},
        calibration_artifact=model.calibration_artifact or {"method": "sigmoid"},
        uncertainty_method="none",
        training_feature_distribution_hash="dist",
        required_input_fields=["spot", "x"],
        dependency_versions={"sklearn": "1.9"},
        git_commit="de4a6e7",
        status="shadow",
    )
    group = registry.save_group(
        component_model_ids={"direction_30m": mid},
        feature_version="v2.0.0",
        label_version="v2.0.0",
        status="shadow",
        group_id="phase5-ok",
    )
    server = ForecastServer(
        registry=registry,
        group_id=group.group_id,
        load_mode="shadow",
    ).load()
    bundle = server.predict(
        snapshot_id="snap-1",
        ts="2026-01-05T10:30:00-05:00",
        session_date="2026-01-05",
        symbol="SPY",
        feature_row={"spot": 500.0, "x": 1.0},
        data_quality=0.9,
        feature_coverage=1.0,
    )
    assert isinstance(bundle, MarketForecastBundle)
    assert bundle.p_up_30m is not None
    assert bundle.forecast_id
    assert bundle.content_hash.startswith("sha256:")


def test_heuristic_not_for_champion_path(tmp_path: object) -> None:
    bundle = heuristic_bundle(
        snapshot_id="s",
        ts="2026-01-05T10:00:00-05:00",
        session_date="2026-01-05",
        net_gex=1.0,
    )
    assert bundle.fallback_state == "heuristic"
    registry = ModelRegistry(directory=str(tmp_path))
    with pytest.raises(ForecastServingError):
        ForecastServer(registry=registry, group_id="missing", load_mode="champion").load()
