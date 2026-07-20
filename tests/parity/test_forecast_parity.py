"""Phase 5 MarketForecastBundle parity fixture (master spec §24, §30, §65).

A fixed feature row is served through a registered direction model and must
reproduce frozen semantic fields from
``baseline/expected_outputs/phase5/forecast_bundle.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from spy_der.contracts import to_canonical_json
from spy_der.forecasting.models.direction import DirectionModel, DirectionModelConfig
from spy_der.forecasting.runtime import ForecastServer
from spy_der.training.registry import ModelRegistry

_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = _ROOT / "baseline" / "expected_outputs" / "phase5" / "forecast_bundle.json"

_FEATURE_ROW = {"spot": 500.0, "x": 1.0, "net_gex": 0.25}


def _train_and_serve(tmp_path: Path) -> object:
    registry = ModelRegistry(directory=str(tmp_path))
    rows = [{"spot": float(100 + i), "x": float(i), "net_gex": 0.1 * (i % 5)} for i in range(80)]
    y = [1 if i % 2 == 0 else 0 for i in range(80)]
    sessions = [f"2026-01-{(i // 10) + 2:02d}" for i in range(80)]
    model = DirectionModel(
        DirectionModelConfig(horizon="30m", c=0.1, l1_ratio=0.5, random_state=7)
    ).fit(rows, y, sessions)
    mid = registry.save(
        model,
        model_type="direction",
        target="up_30m",
        horizon="30m",
        feature_version="v2.0.0",
        label_version="v2.0.0",
        crossfit_config={"outer_folds": 2, "embargo_sessions": 1},
        fold_hash="phase5-parity-fold",
        oof_metrics={"brier": 0.25},
        calibration_artifact=model.calibration_artifact or {"method": "sigmoid"},
        uncertainty_method="none",
        training_feature_distribution_hash="phase5-parity-dist",
        required_input_fields=["spot", "x"],
        dependency_versions={"sklearn": "1.9.0", "numpy": "2.x"},
        git_commit="de4a6e7ced98ff97c778e8b4418c08848d7ce82d",
        status="shadow",
        model_id="direction-up_30m-parity",
    )
    registry.save_group(
        component_model_ids={"direction_30m": mid},
        feature_version="v2.0.0",
        label_version="v2.0.0",
        status="shadow",
        group_id="phase5-parity-group",
    )
    server = ForecastServer(
        registry=registry,
        group_id="phase5-parity-group",
        load_mode="shadow",
    ).load()
    return server.predict(
        snapshot_id="parity-snapshot-001",
        ts="2026-01-05T10:30:00-05:00",
        session_date="2026-01-05",
        symbol="SPY",
        feature_row=_FEATURE_ROW,
        data_quality=0.95,
        feature_coverage=1.0,
    )


def test_forecast_bundle_parity(tmp_path: Path) -> None:
    bundle = _train_and_serve(tmp_path)
    produced = json.loads(to_canonical_json(bundle))
    produced["artifact_hashes"] = {"direction_30m": "parity-artifact"}
    produced["diagnostics"] = {"load_mode": "shadow"}
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    for key in (
        "snapshot_id",
        "ts",
        "session_date",
        "symbol",
        "feature_version",
        "label_version",
        "model_group_id",
        "p_up_30m",
        "data_quality",
        "feature_coverage",
        "fallback_state",
    ):
        assert produced[key] == expected[key]
    assert produced["p_up_30m"] == 0.5
    assert produced["model_versions"]["direction_30m"] == "direction-up_30m-parity"
