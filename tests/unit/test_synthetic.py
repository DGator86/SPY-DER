"""SPY-DER-owned synthetic universe production.

Covers the properties the migration depends on: the world is deterministic, the
coupled dynamics actually behave the way the archetypes claim, the chain is
repriced arbitrage-consistently, the provider emits real candidates through
SPY-DER's own factory, and nothing imports 0DTE.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from spy_der.contracts.market import OptionType
from spy_der.synthetic import (
    ARCHETYPES,
    REGIMES,
    ArchetypeScore,
    MarkovWorld,
    SyntheticUniverseProvider,
    UniverseCatalog,
    UniverseSpec,
    calibrate_from_world,
    evolve_catalog,
    labeler_accuracy,
    merge_coverage,
    next_generation_weights,
    simulator_config,
    simulator_config_hash,
)
from spy_der.synthetic.pricing import (
    black_call_forward,
    black_put_forward,
    norm_cdf,
    smile_vol,
)
from spy_der.synthetic.universe import jitter_for_generation, spec_id


def _spec(archetype: str = "calm_pin", **kw: object) -> UniverseSpec:
    base: dict[str, object] = {
        "universe_id": f"u-{archetype}",
        "seed": 7,
        "days": 1,
        "start_archetype": archetype,
    }
    base.update(kw)
    return UniverseSpec(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Ownership boundary                                                          #
# --------------------------------------------------------------------------- #
FORBIDDEN_0DTE_MODULES = {
    "matrix_universe",
    "synthetic_world",
    "regime_calibration",
    "gate_scorer",
    "unified_loop",
    "rnd_extractor",
    "gex_window",
    "resample",
    "spy0dte",
}


def test_synthetic_package_imports_no_zerodte_modules() -> None:
    """The whole point of the migration: no 0DTE module is reachable from here."""
    root = Path(__file__).resolve().parents[2] / "src" / "spy_der" / "synthetic"
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in FORBIDDEN_0DTE_MODULES, path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in FORBIDDEN_0DTE_MODULES, path


def test_synthetic_package_does_not_import_integrations_zerodte() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "spy_der" / "synthetic"
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "integrations.zerodte" not in text, path


# --------------------------------------------------------------------------- #
# Generative config fingerprint                                               #
# --------------------------------------------------------------------------- #
def test_simulator_config_is_complete_and_hashable() -> None:
    cfg = simulator_config()
    for key in (
        "arch_transition",
        "regime_transition",
        "var_targets",
        "var_preference",
        "breakout_p_up",
        "gen_params",
    ):
        assert cfg[key], key
    assert cfg["owner"] == "spy_der.synthetic"
    assert simulator_config_hash().startswith("sha256:")
    # Stable across calls — a report can be tied to a generative fingerprint.
    assert simulator_config_hash() == simulator_config_hash()


def test_transition_rows_are_proper_distributions() -> None:
    cfg = simulator_config()
    for source, row in cfg["arch_transition"].items():
        assert set(row) == set(ARCHETYPES), source
        assert math.isclose(sum(row.values()), 1.0, abs_tol=1e-9), source
    for archetype, rows in cfg["regime_transition"].items():
        for regime, row in rows.items():
            assert set(row) == set(REGIMES), (archetype, regime)
            assert math.isclose(sum(row.values()), 1.0, abs_tol=1e-9), (archetype, regime)


# --------------------------------------------------------------------------- #
# Determinism                                                                 #
# --------------------------------------------------------------------------- #
def test_same_spec_reproduces_identical_world() -> None:
    a = MarkovWorld(_spec())
    b = MarkovWorld(_spec())
    assert [t.to_dict() for t in a.ticks()] == [t.to_dict() for t in b.ticks()]
    assert a.day_close == b.day_close


def test_different_seed_produces_a_different_world() -> None:
    a = MarkovWorld(_spec(seed=7))
    b = MarkovWorld(_spec(seed=8))
    assert [t.spot for t in a.ticks()] != [t.spot for t in b.ticks()]


def test_generation_zero_carries_no_jitter() -> None:
    """A baseline universe must reproduce pre-migration worlds exactly."""
    assert jitter_for_generation(0) == 0.0
    assert jitter_for_generation(1) > 0.0
    # Capped, so late generations never wander arbitrarily far.
    assert jitter_for_generation(1000) == pytest.approx(0.10)


def test_spec_id_is_stable_and_coordinate_sensitive() -> None:
    assert spec_id(1, "calm_pin", 1.0, 1.0, 0) == spec_id(1, "calm_pin", 1.0, 1.0, 0)
    assert spec_id(1, "calm_pin", 1.0, 1.0, 0) != spec_id(1, "crash", 1.0, 1.0, 0)


# --------------------------------------------------------------------------- #
# The world is coupled, not a random walk                                     #
# --------------------------------------------------------------------------- #
def test_world_generates_a_full_session() -> None:
    world = MarkovWorld(_spec())
    ticks = world.ticks()
    assert len(ticks) == 390
    assert ticks[0].minutes_to_close == 390
    assert ticks[-1].minutes_to_close == 1
    assert list(world.day_close) == ["2026-06-01"]


def test_calm_pin_stays_nearer_its_pin_than_crash_does() -> None:
    """The archetype has to mean something: a pin day is a pin day."""
    calm = MarkovWorld(_spec("calm_pin", seed=11))
    crash = MarkovWorld(_spec("crash", seed=11))
    calm_dev = sum(abs(t.spot - t.pin) for t in calm.ticks()) / 390
    crash_dev = sum(abs(t.spot - t.pin) for t in crash.ticks()) / 390
    assert calm_dev < crash_dev


def test_crash_archetype_finishes_lower_on_average() -> None:
    """Directional archetypes must bias the path, not just the label."""
    net = []
    for seed in range(12):
        world = MarkovWorld(_spec("crash", seed=100 + seed))
        ticks = world.ticks()
        net.append(ticks[-1].spot / ticks[0].spot - 1.0)
    assert sum(net) / len(net) < 0.0


def test_squeeze_archetype_finishes_higher_on_average() -> None:
    net = []
    for seed in range(12):
        world = MarkovWorld(_spec("squeeze_melt_up", seed=200 + seed))
        ticks = world.ticks()
        net.append(ticks[-1].spot / ticks[0].spot - 1.0)
    assert sum(net) / len(net) > 0.0


def test_gamma_flip_follows_the_dealer_convention() -> None:
    """Flip sits below the pin when dealers are long, above spot when short."""
    world = MarkovWorld(_spec("vol_expansion", seed=5))
    checked_long = checked_short = False
    for tick in world.ticks():
        if tick.net_gex > 0:
            assert tick.gamma_flip == pytest.approx(tick.pin - 4.0)
            checked_long = True
        else:
            assert tick.gamma_flip == pytest.approx(tick.spot + 2.0)
            checked_short = True
        if checked_long and checked_short:
            break
    assert checked_long or checked_short


def test_settlement_is_the_realized_close() -> None:
    world = MarkovWorld(_spec())
    ticks = world.ticks()
    assert world.settlement("2026-06-01") == pytest.approx(ticks[-1].spot)
    assert world.settlement("1999-01-01") is None


def test_coverage_records_regime_minutes() -> None:
    world = MarkovWorld(_spec("range_chop", seed=3))
    coverage = world.coverage()
    assert set(coverage) == {"range_chop"}
    assert sum(coverage["range_chop"].values()) == 390


# --------------------------------------------------------------------------- #
# Pricing                                                                     #
# --------------------------------------------------------------------------- #
def test_norm_cdf_matches_known_values() -> None:
    assert norm_cdf(0.0) == pytest.approx(0.5)
    assert norm_cdf(1.96) == pytest.approx(0.9750, abs=1e-4)
    assert norm_cdf(-1.96) == pytest.approx(0.0250, abs=1e-4)


def test_forward_black_respects_put_call_parity() -> None:
    forward, strike, total_vol = 600.0, 595.0, 0.02
    call = black_call_forward(forward, strike, total_vol)
    put = black_put_forward(forward, strike, total_vol)
    assert call - put == pytest.approx(forward - strike, abs=1e-9)


def test_forward_black_is_bounded_by_intrinsic_and_forward() -> None:
    forward, strike = 600.0, 590.0
    call = black_call_forward(forward, strike, 0.03)
    assert forward - strike <= call <= forward


def test_zero_vol_collapses_to_intrinsic() -> None:
    assert black_call_forward(600.0, 590.0, 0.0) == pytest.approx(10.0)
    assert black_call_forward(600.0, 610.0, 0.0) == pytest.approx(0.0)


def test_positive_skew_is_put_heavy() -> None:
    """Sign convention: s(k) = s_atm - skew*k, so positive skew lifts puts."""
    put_side = smile_vol(0.02, skew=0.05, curv=0.0, wing=0.0, k=-0.01, min_vol=1e-6)
    call_side = smile_vol(0.02, skew=0.05, curv=0.0, wing=0.0, k=+0.01, min_vol=1e-6)
    assert put_side > call_side


def test_smile_wings_lift_both_sides() -> None:
    atm = smile_vol(0.02, skew=0.0, curv=0.0, wing=0.5, k=0.0, min_vol=1e-6)
    up = smile_vol(0.02, skew=0.0, curv=0.0, wing=0.5, k=+0.02, min_vol=1e-6)
    down = smile_vol(0.02, skew=0.0, curv=0.0, wing=0.5, k=-0.02, min_vol=1e-6)
    assert up > atm and down > atm


def test_chain_is_repriced_off_current_spot_each_tick() -> None:
    """The frozen-surface bug the coupled world exists to avoid."""
    world = MarkovWorld(_spec("vol_expansion", seed=9))
    ticks = world.ticks()
    early = world.option_chain(ticks[10])
    late = world.option_chain(ticks[380])
    assert early and late
    assert {q.contract.strike for q in early} != {
        q.contract.strike for q in late
    } or [q.mark for q in early] != [q.mark for q in late]


def test_chain_quotes_are_well_formed() -> None:
    world = MarkovWorld(_spec())
    chain = world.option_chain(world.ticks()[60])
    assert chain
    assert {q.contract.option_type for q in chain} == {OptionType.CALL, OptionType.PUT}
    for quote in chain:
        assert quote.bid is not None and quote.ask is not None
        assert quote.bid >= 0
        assert quote.ask >= quote.bid
        assert quote.open_interest is not None and quote.open_interest >= 1
        assert quote.implied_volatility is not None and quote.implied_volatility > 0


def test_call_delta_is_positive_and_put_delta_negative() -> None:
    world = MarkovWorld(_spec())
    for quote in world.option_chain(world.ticks()[60]):
        assert quote.delta is not None
        if quote.contract.option_type is OptionType.CALL:
            assert 0.0 <= quote.delta <= 1.0
        else:
            assert -1.0 <= quote.delta <= 0.0


def test_open_interest_peaks_at_the_pin() -> None:
    world = MarkovWorld(_spec())
    tick = world.ticks()[60]
    calls = [q for q in world.option_chain(tick) if q.contract.option_type is OptionType.CALL]
    peak = max(calls, key=lambda q: q.open_interest or 0)
    assert abs(float(peak.contract.strike) - tick.pin) <= 1.0


# --------------------------------------------------------------------------- #
# The provider                                                                #
# --------------------------------------------------------------------------- #
def test_provider_emits_real_candidates_from_the_spy_der_factory() -> None:
    """The 0DTE bridge emitted one 'unknown'-family placeholder per snapshot."""
    provider = SyntheticUniverseProvider(snapshot_stride=120)
    packets = list(provider.generate(_spec()))
    assert packets
    families = {c.family for p in packets for c in p.candidates}
    assert families
    assert "unknown" not in families
    for packet in packets:
        for candidate in packet.candidates:
            assert candidate.maximum_loss >= 0
            assert candidate.geometry_hash


def test_provider_snapshots_are_not_spuriously_degraded() -> None:
    """A missing settlement provider used to cost every snapshot 0.5 quality."""
    provider = SyntheticUniverseProvider(snapshot_stride=120)
    for packet in provider.generate(_spec()):
        assert packet.data_quality == pytest.approx(1.0)


def test_provider_is_deterministic() -> None:
    provider = SyntheticUniverseProvider(snapshot_stride=120)
    first = [p.to_dict() for p in provider.generate(_spec())]
    second = [p.to_dict() for p in provider.generate(_spec())]
    assert first == second


def test_provider_outcomes_carry_ground_truth() -> None:
    provider = SyntheticUniverseProvider(snapshot_stride=120)
    result = provider.generate_result(_spec("grind_up"))
    assert result.outcomes
    for outcome in result.outcomes:
        assert outcome.settled is True
        labels = outcome.labels
        assert labels["archetype"] == "grind_up"
        assert labels["regime"] in REGIMES
        assert labels["direction"] in {"up", "down", "flat"}


def test_provider_does_not_leak_settlement_into_the_snapshot() -> None:
    """Truth is namespaced so an evaluator can score it without feeding it back."""
    provider = SyntheticUniverseProvider(snapshot_stride=120)
    packet = next(iter(provider.generate(_spec())))
    assert "settlement" not in packet.forecast
    assert "settlement" in packet.forecast["truth"]


def test_provider_honors_max_ticks_and_stride() -> None:
    provider = SyntheticUniverseProvider(snapshot_stride=60, max_ticks=3)
    assert len(list(provider.generate(_spec()))) == 3


def test_provider_accepts_a_duck_typed_spec() -> None:
    class Minimal:
        universe_id = "duck-1"
        archetype = "range_chop"

    provider = SyntheticUniverseProvider(snapshot_stride=200)
    packets = list(provider.generate(Minimal()))
    assert packets
    assert packets[0].snapshot_id.startswith("duck-1-")


def test_provider_rejects_an_unknown_archetype() -> None:
    class Bogus:
        universe_id = "bogus"
        archetype = "trend_up"  # a placeholder name that never existed

    with pytest.raises(ValueError, match="unknown archetype"):
        list(SyntheticUniverseProvider().generate(Bogus()))


# --------------------------------------------------------------------------- #
# Catalog, coverage, evolution                                                #
# --------------------------------------------------------------------------- #
def test_full_lattice_enumerates_every_cell() -> None:
    catalog = UniverseCatalog(seed=1, days=1)
    specs = catalog.full_lattice()
    assert len(specs) == catalog.lattice_size == 8 * 3 * 4
    assert {s.start_archetype for s in specs} == set(ARCHETYPES)
    assert len({s.universe_id for s in specs}) == len(specs)


def test_sample_is_deterministic_and_respects_weights() -> None:
    catalog = UniverseCatalog(seed=42, days=1)
    assert [s.to_dict() for s in catalog.sample(8)] == [
        s.to_dict() for s in catalog.sample(8)
    ]
    only_crash = UniverseCatalog(
        seed=42, days=1, archetype_weights={"crash": 1.0}
    )
    assert {s.start_archetype for s in only_crash.sample(6)} == {"crash"}


def test_merge_coverage_reports_gaps() -> None:
    matrix = merge_coverage([{"calm_pin": {"pin": 300, "compression": 90}}])
    assert matrix.total_cells == 40
    assert matrix.visited_cells == 2
    assert matrix.coverage_fraction == pytest.approx(2 / 40)
    assert ("crash", "breakout") in matrix.unvisited()
    assert ("calm_pin", "pin") not in matrix.unvisited()


def test_evolution_weights_up_the_weak_archetypes() -> None:
    scores = {
        "crash": ArchetypeScore("crash", mean_session_pnl=-50.0, n_sessions=5),
        "calm_pin": ArchetypeScore("calm_pin", mean_session_pnl=+50.0, n_sessions=5),
    }
    plan = next_generation_weights(scores)
    assert plan.weights["crash"] > plan.weights["calm_pin"]


def test_evolution_never_abandons_a_strong_archetype() -> None:
    """Silent regression hides exactly where the agent currently looks good."""
    scores = {
        a: ArchetypeScore(a, mean_session_pnl=+500.0, session_win_rate=1.0, n_sessions=9)
        for a in ARCHETYPES
    }
    plan = next_generation_weights(scores)
    assert all(w > 0.0 for w in plan.weights.values())


def test_unvisited_cells_pull_sampling_even_without_scores() -> None:
    coverage = merge_coverage([{"calm_pin": dict.fromkeys(REGIMES, 100)}])
    plan = next_generation_weights({}, coverage)
    assert plan.weights["crash"] > plan.weights["calm_pin"]


def test_evolve_catalog_advances_generation_and_jitter() -> None:
    catalog = UniverseCatalog(seed=1, days=1, generation=0)
    evolved, plan = evolve_catalog(
        catalog,
        {"crash": ArchetypeScore("crash", mean_session_pnl=-10.0, n_sessions=4)},
    )
    assert evolved.generation == 1
    assert plan.generation == 1
    assert evolved.sample(1)[0].transition_jitter > 0.0


# --------------------------------------------------------------------------- #
# Calibration                                                                 #
# --------------------------------------------------------------------------- #
def test_calibration_returns_proper_transition_matrices() -> None:
    world = MarkovWorld(_spec("range_chop", seed=17, days=3))
    cal = calibrate_from_world(world)
    assert cal.n_sessions == 3
    assert cal.n_minutes > 0
    for row in cal.arch_transition.values():
        assert math.isclose(sum(row.values()), 1.0, abs_tol=1e-9)
    for rows in cal.regime_transition.values():
        for row in rows.values():
            assert math.isclose(sum(row.values()), 1.0, abs_tol=1e-9)
            # Laplace smoothing keeps every transition reachable, so a
            # calibrated matrix can never trap the generator in one state.
            assert all(p > 0.0 for p in row.values())


def test_calibrated_matrices_drive_a_world() -> None:
    source = MarkovWorld(_spec("grind_up", seed=23, days=3))
    cal = calibrate_from_world(source)
    world = MarkovWorld(
        _spec("grind_up", seed=23),
        arch_transition=cal.arch_transition,
        regime_transition=cal.regime_transition,
    )
    assert len(world.ticks()) == 390


def test_labeler_accuracy_is_measured_not_assumed() -> None:
    """Latent-state recovery is approximate; the number has to be reported."""
    world = MarkovWorld(_spec("crash", seed=31, days=2))
    acc = labeler_accuracy(world)
    assert acc["n_sessions"] == 2
    assert acc["n_minutes"] > 0
    assert 0.0 <= acc["regime_accuracy"] <= 1.0
    assert 0.0 <= acc["archetype_accuracy"] <= 1.0


# --------------------------------------------------------------------------- #
# Spec validation                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"days": 0}, "days"),
        ({"tick_stride": 0}, "tick_stride"),
        ({"start_archetype": "nope"}, "unknown archetype"),
        ({"transition_jitter": -1.0}, "transition_jitter"),
    ],
)
def test_universe_spec_validates(kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _spec(**kwargs)


def test_universe_spec_exposes_protocol_archetype_alias() -> None:
    spec = _spec("gap_shock")
    assert spec.archetype == spec.start_archetype == "gap_shock"
