"""Phase 6 unit tests: uncertainty, OOD, conformal, regime, MoE, CR, path, ensemble."""

from __future__ import annotations

import numpy as np
import pytest

from spy_der.contracts import MarketForecastBundle
from spy_der.forecasting.conformal import SplitConformalCalibrator
from spy_der.forecasting.ensemble import ForecastEnsemble
from spy_der.forecasting.models.competing_risk import (
    CompetingRiskModel,
    hazards_to_incidence,
)
from spy_der.forecasting.models.mixture_experts import MixtureOfExperts
from spy_der.forecasting.models.regime_moe import RegimeProbabilityModel, renormalize_probs
from spy_der.forecasting.ood import OODDetector
from spy_der.forecasting.path_model import (
    build_residual_library,
    derive_path_seed,
    forecast_from_paths,
    score_path_events,
    simulate_paths_v3,
)
from spy_der.forecasting.regime_labels import REGIME_CLASSES
from spy_der.forecasting.uncertainty import compose_uncertainty
from spy_der.forecasting.v3 import attach_v3_fields


def test_missing_uncertainty_components_reweight_not_zero() -> None:
    unc = compose_uncertainty(ensemble=0.4, out_of_distribution=None, data_quality=0.2)
    assert unc.out_of_distribution is None
    assert "missing_out_of_distribution_component" in unc.reasons
    assert unc.composite == pytest.approx(0.4 * (0.25 / 0.35) + 0.2 * (0.10 / 0.35))


def test_all_missing_uncertainty_is_one() -> None:
    unc = compose_uncertainty()
    assert unc.composite == 1.0
    assert "no_uncertainty_components_available" in unc.reasons


def test_ood_detector_bounded_and_deterministic() -> None:
    rows = [{"x": float(i), "y": float(i % 5), "realized_vol": 0.01 * i} for i in range(40)]
    det = OODDetector().fit(rows)
    a = det.score_one({"x": 10.0, "y": 1.0, "realized_vol": 0.1})
    b = det.score_one({"x": 10.0, "y": 1.0, "realized_vol": 0.1})
    assert a.score == b.score
    assert 0.0 <= a.score <= 1.0
    assert a.state_bucket


def test_conformal_session_fit_and_ood_widening() -> None:
    y = [0.0, 0.1, -0.05, 0.02]
    lo = [-0.1, -0.1, -0.1, -0.1]
    hi = [0.1, 0.1, 0.1, 0.1]
    sessions = ["s1", "s1", "s2", "s2"]
    cal = SplitConformalCalibrator(nominal_coverage=0.9).fit(y, lo, hi, sessions)
    base = cal.apply(-0.05, 0.05)
    wide = cal.apply(-0.05, 0.05, ood_score=0.95)
    assert wide.upper - wide.lower >= base.upper - base.lower
    assert wide.diagnostics["coverage_limited"] is True


def test_regime_probabilities_sum_to_one() -> None:
    rows = [
        {"gex": 1.0 if i % 4 == 0 else -1.0, "vol": float(i % 3), "x": float(i)}
        for i in range(60)
    ]
    labels = [REGIME_CLASSES[i % 4] for i in range(60)]
    sessions = [f"s{i // 10:02d}" for i in range(60)]
    model = RegimeProbabilityModel()
    model.fit(rows, labels, sessions)
    probs = model.predict(rows[0])
    total = sum(probs.as_dict().values())
    assert total == pytest.approx(1.0, abs=1e-6)
    assert probs.dominant_regime in REGIME_CLASSES


def test_renormalize_probs() -> None:
    out = renormalize_probs({"long_gamma_pin": 2.0, "short_gamma_trend": 2.0})
    assert sum(out.values()) == pytest.approx(1.0)


def test_mixture_uses_full_regime_vector_and_fallback() -> None:
    moe = MixtureOfExperts(target="up", horizon="30m")
    moe.register_global(lambda row: 0.5)
    moe.register_regime_expert(
        "long_gamma_pin", lambda row: 0.8, support_sessions=50.0, n_rows=600.0
    )
    # other regimes insufficient → fall back to global
    forecast = moe.predict(
        {"x": 1.0},
        {
            "long_gamma_pin": 0.4,
            "short_gamma_trend": 0.2,
            "flip_transition": 0.2,
            "volatility_expansion": 0.2,
        },
    )
    assert isinstance(forecast.final_prediction, float)
    assert abs(sum(forecast.regime_probabilities.values()) - 1.0) < 1e-9
    assert forecast.expert_weights["long_gamma_pin"] > 0


def test_competing_risk_probabilities_sum_and_survival() -> None:
    inc = hazards_to_incidence([0.1, 0.1, 0.1], [0.05, 0.05, 0.05])
    total = inc["p_target_first"] + inc["p_stop_first"] + inc["p_neither"]
    assert total == pytest.approx(1.0, abs=1e-9)
    surv = inc["survival"]
    assert all(surv[i] >= surv[i + 1] - 1e-12 for i in range(len(surv) - 1))

    rows = [{"dist": float(i), "vol": 0.01 * (i % 5)} for i in range(90)]
    y = [i % 3 for i in range(90)]
    model = CompetingRiskModel().fit(rows, y, sessions=[f"s{i // 15}" for i in range(90)])
    fc = model.forecast_from_path_features(rows[:10])
    assert fc.p_target_first + fc.p_stop_first + fc.p_neither == pytest.approx(1.0, abs=1e-5)


def test_path_seed_deterministic_and_adverse_first() -> None:
    a = derive_path_seed("snap-1", horizon="30m", configuration_hash="abc")
    b = derive_path_seed("snap-1", horizon="30m", configuration_hash="abc")
    assert a == b
    assert derive_path_seed("snap-2", horizon="30m", configuration_hash="abc") != a

    # Construct two-step path that hits both target and stop in one step
    paths = np.array([[100.0, 100.0], [100.0, 100.0]])
    # Force a step that spans both barriers via high/low from 95 to 105
    paths = np.array([[100.0, 105.0], [100.0, 95.0]])
    # Better: same path goes from 100 to 110 then we'll use single step 90->110
    paths = np.array([[100.0, 110.0], [100.0, 110.0]])
    events = score_path_events(paths, spot=100.0, target=105.0, stop=95.0)
    # First path 100->110 hits target only; craft ambiguous: 100 -> 90 via going through
    # Using close-to-close envelope: prev=100, curr=90 => lo=90, hi=100 — hits stop only.
    # Ambiguous: prev=100, curr=100 but we need hi>=target and lo<=stop.
    # With close-to-close, use prev=96, curr=106 => lo=96, hi=106 if spot mid...
    paths = np.array([[100.0, 106.0]])  # lo=100, hi=106 — target only if stop below 100
    # True ambiguity: prev=100, curr=100 isn't enough. System uses min/max of prev,curr.
    # So prev=94, curr=106 => lo=94, hi=106 hits both target 105 and stop 95.
    paths = np.array([[100.0, 106.0], [100.0, 94.0], [100.0, 106.0]])
    # Wait path[0]: 100->106: lo=100,hi=106 — target only
    # For ambiguity need a single step covering both: start at 100, end at 106 doesn't hit stop 95.
    # start 100 end 90: lo=90,hi=100 — stop only
    # Need start between? Actually start must be spot on col0. So col0=100.
    # Same-side barriers: target=102, stop=101, up=True.
    # From 100->103: hi=103, lo=100 hits both → adverse-first counts stop.
    paths = np.array([[100.0, 103.0], [100.0, 103.0], [100.0, 100.5]])
    events = score_path_events(paths, spot=100.0, target=102.0, stop=101.0)
    # First two paths ambiguous → counted as stop (adverse-first)
    assert events.p_stop_first >= events.p_target_first
    assert events.ambiguous_same_step_rate > 0


def test_simulate_paths_deterministic() -> None:
    lib = build_residual_library(
        {f"s{i}": list(np.random.default_rng(i).normal(0, 0.001, size=80)) for i in range(5)}
    )
    p1, d1 = simulate_paths_v3(
        500.0, 10, 0.001, library=lib, snapshot_id="snap-a", horizon="30m", mode="test"
    )
    p2, d2 = simulate_paths_v3(
        500.0, 10, 0.001, library=lib, snapshot_id="snap-a", horizon="30m", mode="test"
    )
    assert np.allclose(p1, p2)
    assert d1["seed"] == d2["seed"]
    fc = forecast_from_paths(p1, spot=500.0, target=505.0, stop=495.0, diagnostics=d1)
    assert fc.p_target_first + fc.p_stop_first + fc.p_neither == pytest.approx(1.0, abs=1e-6)


def test_ensemble_excludes_missing_and_reweights() -> None:
    ens = ForecastEnsemble(target="up", horizon="30m")
    out = ens.combine(
        {"a": 0.6, "b": None, "c": 0.4},
        oos_losses={"a": 0.2, "c": 0.3},
        artifact_load_failures=["b"],
    )
    assert "b" in out.missing_components
    assert abs(sum(out.component_weights.values()) - 1.0) < 1e-9
    assert "b" not in out.component_weights


def test_attach_v3_fields_to_bundle() -> None:
    base = MarketForecastBundle(
        snapshot_id="snap-v3",
        ts="2026-01-05T10:30:00-05:00",
        session_date="2026-01-05",
        p_up_30m=0.55,
    )
    unc = compose_uncertainty(ensemble=0.3, data_quality=0.1)
    ood = OODDetector().fit([{"x": 1.0}, {"x": 2.0}]).score_one({"x": 1.5})
    regime = RegimeProbabilityModel()
    rows = [{"x": float(i), "gex": float((-1) ** i)} for i in range(40)]
    labels = [REGIME_CLASSES[i % 4] for i in range(40)]
    sessions = [f"s{i // 8}" for i in range(40)]
    regime.fit(rows, labels, sessions)
    rp = regime.predict(rows[0])
    bundle = attach_v3_fields(base, uncertainty=unc, ood=ood, regime=rp)
    assert bundle.uncertainty == unc.composite
    assert bundle.ood_score == ood.score
    assert bundle.dominant_regime == rp.dominant_regime
    assert sum(bundle.regime_probabilities.values()) == pytest.approx(1.0, abs=1e-6)
