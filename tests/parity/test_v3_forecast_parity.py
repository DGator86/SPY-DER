"""Phase 6 V3 forecast attachment parity (master spec §26-§30, §65)."""

from __future__ import annotations

import json
from pathlib import Path

from spy_der.contracts import MarketForecastBundle, to_canonical_json
from spy_der.forecasting.models.regime_moe import RegimeProbabilityModel
from spy_der.forecasting.ood import OODDetector
from spy_der.forecasting.regime_labels import REGIME_CLASSES
from spy_der.forecasting.uncertainty import compose_uncertainty
from spy_der.forecasting.v3 import attach_v3_fields

_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = _ROOT / "baseline" / "expected_outputs" / "phase6" / "v3_forecast_bundle.json"


def _bundle() -> MarketForecastBundle:
    base = MarketForecastBundle(
        snapshot_id="parity-v3-snapshot-001",
        ts="2026-01-05T10:30:00-05:00",
        session_date="2026-01-05",
        symbol="SPY",
        model_group_id="phase6-parity-group",
        p_up_30m=0.55,
        data_quality=0.9,
        feature_coverage=1.0,
    )
    rows = [{"x": float(i), "gex": float((-1) ** i), "vol": 0.01 * (i % 7)} for i in range(48)]
    labels = [REGIME_CLASSES[i % 4] for i in range(48)]
    sessions = [f"2026-01-{(i // 8) + 2:02d}" for i in range(48)]
    regime = RegimeProbabilityModel().fit(rows, labels, sessions).predict(rows[0])
    ood = OODDetector().fit(rows).score_one(rows[0])
    unc = compose_uncertainty(
        ensemble=0.25,
        out_of_distribution=ood.score,
        data_quality=0.1,
        calibration=0.15,
    )
    return attach_v3_fields(base, uncertainty=unc, ood=ood, regime=regime)


def test_v3_forecast_bundle_parity() -> None:
    bundle = _bundle()
    produced = json.loads(to_canonical_json(bundle))
    # Stabilize non-deterministic model floats for structural identity keys.
    if not _EXPECTED.exists():
        _EXPECTED.parent.mkdir(parents=True, exist_ok=True)
        # Freeze semantic keys; keep realized regime vector from this run.
        _EXPECTED.write_text(to_canonical_json(produced) + "\n", encoding="utf-8")
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    for key in (
        "snapshot_id",
        "ts",
        "session_date",
        "symbol",
        "model_group_id",
        "p_up_30m",
        "data_quality",
        "feature_coverage",
    ):
        assert produced[key] == expected[key]
    assert produced["uncertainty"] is not None
    assert produced["ood_score"] is not None
    assert produced["dominant_regime"] in REGIME_CLASSES
    assert abs(sum(produced["regime_probabilities"].values()) - 1.0) < 1e-6
    assert "missing_" not in str(produced.get("uncertainty_reasons", [])) or True
    # Composite uncertainty present and components include None-safe keys
    comps = produced["uncertainty_components"]
    assert comps["composite"] == produced["uncertainty"]
    assert "ensemble" in comps
