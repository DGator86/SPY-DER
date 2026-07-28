"""Recordings -> per-candidate settlement rows -> a fitted candidate-value model.

This closes the gap that let SPY-DER avoid losing trades without being able to
choose winning ones. Economics computes an `expected_value` only when a caller
supplies `expected_net_pnl`, and that comes from `CandidateValueModel` — which
nothing ever fitted. So every candidate reached the decision layer with
`utility=None`, `DeterministicDecisionAgent` fell through to its candidate-id
tiebreak, and the Dojo scored an alphabetical pick.

The load-bearing test is `test_the_tape_ranks_by_value_once_a_model_is_attached`:
with a model, `utility` is populated and `v3_rank` becomes a real ordering;
without one, both stay absent rather than presenting the alphabet as a ranking.

Leakage matters more here than for the forecast models. Every candidate at a
tick settles against one closing price, so a random split can put the same
settlement on both sides of the boundary — hence whole-session folds.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest
from test_dojo_native_tape import _record, _session_snapshots

from spy_der.dojo.native_tape import NativeTapeProvider, load_value_model
from spy_der.training.candidate_value import (
    CandidateValueObservations,
    build_candidate_observations,
    train_candidate_value_model,
)
from spy_der.training.registry import ModelRegistry


# --------------------------------------------------------------------------- #
# Observations                                                                #
# --------------------------------------------------------------------------- #
def test_recordings_become_per_candidate_settlement_rows(tmp_path: Path) -> None:
    _record(tmp_path, sessions=2)
    observations = build_candidate_observations(tmp_path, interval_minutes=0)

    assert len(observations) > 0
    assert len(observations.sessions) == 2
    # One row per candidate per tick, so rows far outnumber ticks.
    assert len(observations.rows) == len(observations.y_pnl) == len(observations.y_profit)
    assert len(observations.row_sessions) == len(observations.rows)
    assert set(observations.row_sessions) == set(observations.sessions)


def test_the_target_is_settlement_not_a_proxy(tmp_path: Path) -> None:
    """`y_profit` must agree with `y_pnl` — they come from one settlement."""
    _record(tmp_path, sessions=1)
    observations = build_candidate_observations(tmp_path, interval_minutes=0)

    for pnl, win in zip(observations.y_pnl, observations.y_profit, strict=True):
        assert win == (1 if pnl > 0 else 0)


def test_rows_carry_the_economics_the_model_needs(tmp_path: Path) -> None:
    _record(tmp_path, sessions=1)
    observations = build_candidate_observations(tmp_path, interval_minutes=0)

    row = observations.rows[0]
    for field in ("maximum_loss", "fill_probability", "entry_credit", "liquidity_score"):
        assert field in row, field


def test_an_unfinished_session_contributes_no_rows(tmp_path: Path) -> None:
    """No close, no settlement, no target.

    Fitting against a midday quote would teach the model that every position
    expires at lunchtime.
    """
    _record(tmp_path, sessions=1, bars=120, ticks=10, every=4)  # stops ~11:30 ET
    observations = build_candidate_observations(tmp_path, interval_minutes=0)

    assert len(observations) == 0
    assert any("close" in why for _, why in observations.skipped_sessions)


def test_sampling_is_by_wall_clock(tmp_path: Path) -> None:
    _record(tmp_path, sessions=1, ticks=40, every=4)
    every = len(build_candidate_observations(tmp_path, interval_minutes=0))
    spaced = len(build_candidate_observations(tmp_path, interval_minutes=30))
    assert every > spaced > 0


def test_a_corrupt_recording_costs_its_session_not_the_run(tmp_path: Path) -> None:
    sessions = _record(tmp_path, sessions=2)
    (tmp_path / "market" / f"{sessions[0].isoformat()}.jsonl").write_text(
        "not a record\n", encoding="utf-8"
    )
    observations = build_candidate_observations(tmp_path, interval_minutes=0)

    assert observations.sessions == (sessions[1].isoformat(),)
    assert any("corrupt" in why for _, why in observations.skipped_sessions)


# --------------------------------------------------------------------------- #
# Training                                                                    #
# --------------------------------------------------------------------------- #
def test_too_few_rows_is_refused_not_fitted(tmp_path: Path) -> None:
    """A Huber regression over ~20 features will fit 50 rows and generalize to nothing."""
    _record(tmp_path, sessions=1, ticks=3)
    observations = build_candidate_observations(tmp_path, interval_minutes=0)
    registry = ModelRegistry(str(tmp_path / "models"))

    result = train_candidate_value_model(observations, registry=registry)

    assert result.model_id is None
    assert result.status == "skipped"
    assert "rows" in result.skipped_reason


def test_a_single_sided_target_is_refused(tmp_path: Path) -> None:
    """Every candidate settling the same way makes the classifier a constant."""
    observations = CandidateValueObservations(
        rows=[{"maximum_loss": float(i), "entry_credit": 1.0} for i in range(500)],
        y_pnl=[1.0] * 500,
        y_profit=[1] * 500,
        row_sessions=["2026-01-05"] * 500,
        sessions=("2026-01-05",),
    )
    result = train_candidate_value_model(
        observations, registry=ModelRegistry(str(Path("/tmp") / "unused-registry"))
    )

    assert result.model_id is None
    assert "same side of zero" in result.skipped_reason


@pytest.fixture(scope="module")
def trained(tmp_path_factory: pytest.TempPathFactory):
    """Train once — building candidate rows over many sessions is not cheap."""
    root = tmp_path_factory.mktemp("value-state")
    _record(root, sessions=14, ticks=14, every=26)
    observations = build_candidate_observations(root, interval_minutes=0)
    registry = ModelRegistry(str(root / "models"))
    result = train_candidate_value_model(observations, registry=registry, min_rows=100)
    return root, observations, result


def test_training_registers_a_model(trained) -> None:
    _root, observations, result = trained
    assert result.model_id is not None, result.skipped_reason
    assert result.model_id.startswith("candidate_value-")
    assert result.n_rows == len(observations)


def test_the_model_is_research_status_and_cannot_promote_itself(trained) -> None:
    root, _obs, result = trained
    registry = ModelRegistry(str(root / "models"))
    meta = registry.load_metadata(result.model_id)

    assert meta["status"] == "research"
    assert meta["target"] == "settled_net_pnl"
    with pytest.raises(Exception, match="load_mode"):
        registry.load(result.model_id, load_mode="champion")


def test_metrics_are_out_of_fold_or_absent(trained) -> None:
    """An unearned metric is worse than a missing one."""
    root, _obs, result = trained
    meta = ModelRegistry(str(root / "models")).load_metadata(result.model_id)

    assert meta["oof_metrics"] == result.oof_metrics
    if result.oof_metrics:
        assert meta["crossfit_config"]["n_folds"] > 0
        assert meta["crossfit_config"]["scheme"] == "expanding_session_walk_forward"


def test_the_audit_fields_a_promotion_reads_are_recorded(trained) -> None:
    root, _obs, result = trained
    meta = ModelRegistry(str(root / "models")).load_metadata(result.model_id)

    assert meta["required_input_fields"]
    assert meta["training_feature_distribution_hash"]
    assert meta["dependency_versions"]["python"]
    assert meta["calibration_artifact"]["method"] == "none"
    assert meta["calibration_artifact"]["reason"]


# --------------------------------------------------------------------------- #
# Skill is measured against a no-features baseline                            #
# --------------------------------------------------------------------------- #
def _synthetic(n_sessions: int, *, signal: bool) -> CandidateValueObservations:
    """Rows whose P&L either does or does not depend on the features."""
    rng = np.random.default_rng(11)
    obs = CandidateValueObservations()
    sessions: list[str] = []
    for s in range(n_sessions):
        label = date(2026, 1, 5 + s).isoformat()
        sessions.append(label)
        for _ in range(60):
            loss = float(rng.uniform(0.5, 5.0))
            credit = float(rng.uniform(-2.0, 2.0))
            noise = float(rng.normal(0.0, 0.4))
            pnl = (credit - 0.3 * loss + noise) if signal else noise
            obs.rows.append(
                {
                    "maximum_loss": loss,
                    "entry_credit": credit,
                    "fill_probability": float(rng.uniform(0.3, 1.0)),
                }
            )
            obs.y_pnl.append(pnl)
            obs.y_profit.append(1 if pnl > 0 else 0)
            obs.row_sessions.append(label)
    obs.sessions = tuple(sessions)
    return obs


def test_a_learnable_target_reports_positive_skill(tmp_path: Path) -> None:
    result = train_candidate_value_model(
        _synthetic(14, signal=True),
        registry=ModelRegistry(str(tmp_path / "models")),
        min_rows=100,
    )
    assert result.model_id is not None
    assert result.oof_metrics, "walk-forward folds should have formed"
    assert result.oof_metrics["mae_skill"] > 0.0
    assert result.oof_metrics["selection_edge"] > 0.0


def test_pure_noise_does_not_report_skill(tmp_path: Path) -> None:
    """The number that stops a useless model being mistaken for a working one.

    A raw MAE looks respectable on noise. Skill against the unconditional
    median does not.
    """
    result = train_candidate_value_model(
        _synthetic(14, signal=False),
        registry=ModelRegistry(str(tmp_path / "models")),
        min_rows=100,
    )
    assert result.model_id is not None
    assert result.oof_metrics
    assert result.oof_metrics["mae_skill"] < 0.05
    assert result.oof_metrics["selection_edge"] < 0.15


def test_folds_are_absent_rather_than_in_sample_when_history_is_short(
    tmp_path: Path,
) -> None:
    result = train_candidate_value_model(
        _synthetic(4, signal=True),
        registry=ModelRegistry(str(tmp_path / "models")),
        min_rows=100,
    )
    assert result.model_id is not None
    assert result.oof_metrics == {}


# --------------------------------------------------------------------------- #
# Serving it back into the tape                                               #
# --------------------------------------------------------------------------- #
def test_the_tape_ranks_by_value_once_a_model_is_attached(trained) -> None:
    """The whole point: with a model the Dojo measures selection, not the alphabet."""
    root, _obs, result = trained
    assert result.model_id is not None
    model, note = load_value_model(root)
    assert model is not None, note

    session = NativeTapeProvider(root).sessions()[0]
    provider = NativeTapeProvider(root, interval_minutes=0, value_model=model)
    packets = list(provider.snapshots(session))

    assert packets
    priced = [c for p in packets for c in p.candidates if c.utility is not None]
    assert priced, "a fitted model must give candidates a utility"
    packet = packets[0]
    assert [c.v3_rank for c in packet.candidates] == list(
        range(1, len(packet.candidates) + 1)
    )
    utilities = [c.utility for c in packet.candidates if c.utility is not None]
    assert utilities == sorted(utilities, reverse=True)
    assert not [w for w in provider.warnings() if "tape_unpriced" in w]


def test_without_a_model_the_tape_still_refuses_to_fake_a_ranking(trained) -> None:
    root, _obs, _result = trained
    session = NativeTapeProvider(root).sessions()[0]
    provider = NativeTapeProvider(root, interval_minutes=0, value_model=None)
    packets = list(provider.snapshots(session))

    assert all(c.utility is None for p in packets for c in p.candidates)
    assert all(c.v3_rank is None for p in packets for c in p.candidates)
    assert any("tape_unpriced" in w for w in provider.warnings())


def test_loading_reports_absence_rather_than_raising(tmp_path: Path) -> None:
    """An unfitted registry is the normal early state, not a broken run."""
    model, note = load_value_model(tmp_path)
    assert model is None
    assert "registry" in note or "no candidate-value model" in note

    (tmp_path / "models").mkdir()
    model, note = load_value_model(tmp_path)
    assert model is None
    assert "no candidate-value model registered" in note


def test_a_failing_model_costs_its_candidate_not_the_tick(tmp_path: Path) -> None:
    """One unscoreable candidate must not take down the whole snapshot."""
    session = _record(tmp_path, sessions=1)[0]

    class _Boom:
        def predict_one(self, *_a: object, **_k: object) -> object:
            raise RuntimeError("predict before fit")

    provider = NativeTapeProvider(tmp_path, interval_minutes=0, value_model=_Boom())
    packets = list(provider.snapshots(session))

    assert packets, "the tick must survive a failing value model"
    assert all(c.utility is None for p in packets for c in p.candidates)


def test_snapshots_without_a_chain_are_skipped_before_valuation(tmp_path: Path) -> None:
    """Guard the ordering: no candidates means no economics to value."""
    import json

    from spy_der.contracts.market import CanonicalMarketSnapshot
    from spy_der.market_data.recording import build_record

    market = tmp_path / "market"
    market.mkdir(parents=True)
    session = date(2026, 1, 5)
    stripped = [
        CanonicalMarketSnapshot(
            **{
                **{
                    f.name: getattr(s, f.name)
                    for f in s.__dataclass_fields__.values()  # type: ignore[attr-defined]
                },
                "option_chain": (),
            }
        )
        for s in _session_snapshots(session, seed=5, ticks=4)
    ]
    with (market / f"{session.isoformat()}.jsonl").open("w", encoding="utf-8") as fh:
        for seq, snap in enumerate(stripped):
            fh.write(json.dumps(build_record(seq, snap), sort_keys=True) + "\n")

    observations = build_candidate_observations(tmp_path, interval_minutes=0)
    assert len(observations) == 0
