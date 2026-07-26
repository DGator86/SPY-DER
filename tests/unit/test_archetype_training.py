"""Training the gaps — the robustness matrix has to change what the Dojo does.

The universe panel could always say "crash is underwater". These tests pin the
three hops that were missing between saying it and doing something about it:
the lattice spends its next generation there, the learner diagnoses it, and a
change staged to repair it is held to that archetype before it can promote.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from spy_der.dojo.config import DojoConfig
from spy_der.dojo.universe import cfg_weights, default_catalog
from spy_der.learning.gaps import (
    ArchetypeGap,
    load_archetype_gaps,
    record_archetype_gaps,
    sampling_weights,
    weakest_archetypes,
)
from spy_der.learning.hypotheses import (
    diagnose,
    generate_hypotheses,
    target_archetype_of,
)
from spy_der.learning.memories import append_failure_episode
from spy_der.learning.promotion_trial import run_promotion_trial

# The panel from the production report the loop was failing on: profitable
# overall, five archetypes underwater, and nothing being done about it.
PANEL: dict[str, Any] = {
    "status": "ok",
    "archetype_matrix": {
        "calm_pin": {
            "mean_session_pnl": 67.511, "total_pnl": 67.511,
            "n_sessions": 1, "n_universes": 1,
        },
        "crash": {
            "mean_session_pnl": -102.368, "total_pnl": -307.103,
            "n_sessions": 3, "n_universes": 3,
        },
        "range_chop": {
            "mean_session_pnl": -13.503, "total_pnl": -67.515,
            "n_sessions": 5, "n_universes": 5,
        },
        "squeeze_melt_up": {
            "mean_session_pnl": -108.873, "total_pnl": -108.873,
            "n_sessions": 1, "n_universes": 1,
        },
        "vol_expansion": {
            "mean_session_pnl": 49.836, "total_pnl": 199.343,
            "n_sessions": 4, "n_universes": 4,
        },
    },
}


# --------------------------------------------------------------------------- #
# Remembering the gaps                                                        #
# --------------------------------------------------------------------------- #
def test_gaps_are_recorded_worst_first_and_read_back(tmp_path: Path) -> None:
    gaps = record_archetype_gaps(tmp_path, PANEL, report_date="2026-07-25")
    assert [g.archetype for g in gaps] == ["crash", "range_chop"]
    # squeeze_melt_up loses more per session but has one session behind it —
    # severity discounts thin evidence rather than acting on a single world.
    assert "squeeze_melt_up" not in {g.archetype for g in gaps}

    reloaded = load_archetype_gaps(tmp_path)
    assert [g.archetype for g in reloaded] == ["crash", "range_chop"]
    assert reloaded[0].mean_session_pnl == pytest.approx(-102.368)
    assert reloaded[0].n_sessions == 3
    assert reloaded[0].report_date == "2026-07-25"


def test_gaps_are_replaced_not_appended(tmp_path: Path) -> None:
    record_archetype_gaps(tmp_path, PANEL)
    record_archetype_gaps(tmp_path, PANEL)
    ids = [g.archetype for g in load_archetype_gaps(tmp_path)]
    assert len(ids) == len(set(ids))


def test_stale_gaps_stop_being_trained_on(tmp_path: Path) -> None:
    old = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    append_failure_episode(
        tmp_path,
        episode_id="weak-archetype-ancient",
        summary="stale",
        details={
            "kind": "weak_archetype", "archetype": "ancient",
            "mean_session_pnl": -50.0, "n_sessions": 9,
        },
    )
    # Rewrite the stored timestamp to make it old.
    store = tmp_path / "memories" / "failure_episodes.json"
    store.write_text(store.read_text(encoding="utf-8").replace(
        datetime.now(UTC).isoformat()[:10], old[:10]
    ), encoding="utf-8")
    assert [g.archetype for g in load_archetype_gaps(tmp_path, max_age_days=14)] == []


def test_a_repaired_archetype_leaves_the_training_set(tmp_path: Path) -> None:
    record_archetype_gaps(tmp_path, PANEL)
    repaired = {
        "status": "ok",
        "archetype_matrix": {
            **PANEL["archetype_matrix"],
            "crash": {
                "mean_session_pnl": 12.0, "total_pnl": 36.0,
                "n_sessions": 3, "n_universes": 3,
            },
        },
    }
    still_weak = [g.archetype for g in record_archetype_gaps(tmp_path, repaired)]
    assert "crash" not in still_weak


# --------------------------------------------------------------------------- #
# Spending the lattice where the gaps are                                     #
# --------------------------------------------------------------------------- #
def test_gaps_bias_the_lattice_toward_the_weak_archetypes() -> None:
    gaps = [
        ArchetypeGap("crash", -102.0, -307.0, 3, 3, datetime.now(UTC)),
        ArchetypeGap("range_chop", -13.5, -67.5, 5, 5, datetime.now(UTC)),
    ]
    weights = sampling_weights(gaps)
    assert weights["crash"] > weights["range_chop"] > 1.0

    cfg = DojoConfig(universes_per_gen=40, universe_days=1)
    unweighted = default_catalog(cfg, 0, archetype_weights=cfg_weights(cfg))
    weighted = default_catalog(cfg, 0, archetype_weights={**cfg_weights(cfg), **weights})
    crash_before = sum(1 for s in unweighted if s.start_archetype == "crash")
    crash_after = sum(1 for s in weighted if s.start_archetype == "crash")
    assert crash_after > crash_before, (crash_before, crash_after)


def test_generations_actually_re_weight(tmp_path: Path) -> None:
    """The report claimed each generation targets the weakest archetypes.

    It did not: every generation was drawn with uniform weights and differed
    only by Dirichlet jitter, so a gap never got more training data than the
    parts already working.
    """
    from spy_der.dojo.runner import run_dojo

    cfg = DojoConfig(
        reports_dir=str(tmp_path / "reports"),
        configs_dir=str(tmp_path / "configs"),
        report_date="2026-07-26",
        universes_per_gen=3,
        generations=2,
        universe_days=1,
        universe_snapshot_stride=120,
        force_universe=True,
    )
    out = run_dojo(cfg)
    universe = out["metrics"]["phases"]["universe"]
    applied = universe["evolution_applied"]
    assert applied, "no re-weighting was applied between generations"
    assert applied[0]["generation"] == 1
    # Weights are not all equal — some archetype earned more draws than others.
    weights = applied[0]["weights"]
    assert len(set(round(v, 4) for v in weights.values())) > 1


# --------------------------------------------------------------------------- #
# Diagnosing them                                                             #
# --------------------------------------------------------------------------- #
def test_profitable_tape_with_a_weak_archetype_is_not_stable_baseline() -> None:
    healthy = {"n_sessions": 8, "evaluation": {"win_rate": 0.83, "total_pnl": 77.0}}
    assert diagnose(healthy) == ["stable_baseline"]
    with_gap = diagnose(healthy, weak_archetypes=("crash", "range_chop"))
    assert with_gap == ["weak_archetype:crash", "weak_archetype:range_chop"]
    assert "stable_baseline" not in with_gap


def test_weak_archetype_hypotheses_target_that_archetype() -> None:
    hypotheses = generate_hypotheses(diagnose(
        {"n_sessions": 8, "evaluation": {"win_rate": 0.83, "total_pnl": 77.0}},
        weak_archetypes=("crash",),
    ))
    assert hypotheses, "a weak archetype produced no hypothesis to try"
    assert all(h.target_archetype == "crash" for h in hypotheses)
    # Each carries a live knob — a hold_champion would stage nothing.
    assert all(h.change for h in hypotheses)
    top = max(hypotheses, key=lambda h: h.priority)
    assert top.change == {"prefer_abstain_on_ood": True}
    assert target_archetype_of(top.diagnosis) == "crash"


def test_weakest_archetypes_is_bounded() -> None:
    gaps = [
        ArchetypeGap(f"a{i}", -10.0 * (9 - i), -10.0, 5, 5, datetime.now(UTC))
        for i in range(8)
    ]
    assert weakest_archetypes(gaps, limit=2) == ("a0", "a1")


# --------------------------------------------------------------------------- #
# Holding the fix to the gap it was staged for                                #
# --------------------------------------------------------------------------- #
def _panel_with(challenger_crash: float, champion_crash: float) -> dict[str, Any]:
    return {
        "status": "ok",
        "n_scored_universes": 6,
        "authorities": {
            "champion": {"total_pnl": 10.0, "trades": 100, "n_universes": 6},
            "challenger": {"total_pnl": 40.0, "trades": 60, "n_universes": 6},
        },
        "archetype_authorities": {
            "crash": {
                "champion": {"total_pnl": champion_crash, "trades": 50, "win_rate": 0.2},
                "challenger": {"total_pnl": challenger_crash, "trades": 20, "win_rate": 0.5},
            }
        },
    }


def test_promotion_refuses_a_change_that_does_not_repair_its_target(
    tmp_path: Path,
) -> None:
    from test_promotion_trial import _cfg, _seed_ood_tape

    experience = _seed_ood_tape(tmp_path / "experience")
    trial = run_promotion_trial(
        _cfg(tmp_path),
        changes={"prefer_abstain_on_ood": True},
        candidate_id="dojo-target-crash",
        experience=experience,
        universe_result=_panel_with(challenger_crash=-120.0, champion_crash=-100.0),
        target_archetype="crash",
    )
    assert trial.status == "rejected"
    assert trial.blocking_gate == "archetype_repair"
    # The aggregate improved — that is exactly the case this gate exists for.
    assert "crash" in (trial.note or "")


def test_promotion_accepts_a_change_that_repairs_its_target(tmp_path: Path) -> None:
    from test_promotion_trial import _cfg, _seed_ood_tape

    experience = _seed_ood_tape(tmp_path / "experience")
    trial = run_promotion_trial(
        _cfg(tmp_path),
        changes={"prefer_abstain_on_ood": True},
        candidate_id="dojo-target-crash",
        experience=experience,
        universe_result=_panel_with(challenger_crash=-20.0, champion_crash=-100.0),
        target_archetype="crash",
    )
    assert trial.status == "validated", trial.note
    assert trial.target_archetype == "crash"
    repair = next(g for g in trial.gates if g.name == "archetype_repair")
    assert repair.passed
    assert "-100.0000 → -20.0000" in repair.detail


def _seed_profitable_tape(root: Path, *, ticks: int = 40) -> Any:
    """Real tape that makes money, half of it in high-uncertainty ticks.

    The production case: the recorded average looks healthy, so nothing in the
    aggregate asks for a change — the only thing wrong is in the archetypes.
    """
    import json
    from datetime import date, datetime
    from decimal import Decimal

    from spy_der.contracts.integration import (
        MARKET_PACKET_SCHEMA,
        OUTCOME_PACKET_SCHEMA,
        MarketCandidateView,
        MarketPacket,
        OutcomePacket,
    )
    from spy_der.integrations.zerodte.recorded_feed import FileMarketExperienceProvider

    sessions = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23"]
    (root / "snapshots").mkdir(parents=True)
    (root / "outcomes").mkdir(parents=True)
    candidate = MarketCandidateView(
        candidate_id="c1",
        family="long_call",
        direction="bullish",
        maximum_loss=Decimal("1"),
        capital_required=Decimal("1"),
        geometry_hash="sha256:c1",
        expiration=date(2026, 7, 22),
        utility=1.0,
    )
    for session in sessions:
        for tick in range(ticks):
            snap_id = f"snap-{session}-{tick}"
            packet = MarketPacket(
                schema_version=MARKET_PACKET_SCHEMA,
                snapshot_id=snap_id,
                session_date=date.fromisoformat(session),
                symbol="SPY",
                underlying_price=Decimal("600"),
                data_quality=1.0,
                forecast_uncertainty=0.9 if tick % 2 == 0 else 0.1,
                candidates=(candidate,),
                forecast={
                    "labels": {
                        "realized_pnl": 0.10,
                        "true_direction": "bullish",
                        "realized_pnl_by_candidate": {"c1": 0.10},
                    }
                },
                generated_at=datetime(2026, 7, 22, 15, 0, tzinfo=UTC),
            )
            (root / "snapshots" / f"{snap_id}.json").write_text(
                json.dumps(packet.to_dict()), encoding="utf-8"
            )
            outcome = OutcomePacket(
                schema_version=OUTCOME_PACKET_SCHEMA,
                snapshot_id=snap_id,
                session_date=date.fromisoformat(session),
                symbol="SPY",
                candidate_id="c1",
                action="TRADE",
                realized_pnl=Decimal("0.10"),
                settled=True,
                labels={
                    "true_direction": "bullish",
                    "realized_pnl_by_candidate": {"c1": 0.10},
                },
                settled_at=datetime(2026, 7, 22, 20, 0, tzinfo=UTC),
            )
            (root / "outcomes" / f"{snap_id}.json").write_text(
                json.dumps(outcome.to_dict()), encoding="utf-8"
            )
    (root / "sessions.json").write_text(json.dumps(sessions), encoding="utf-8")
    return FileMarketExperienceProvider(root)


def test_end_to_end_a_remembered_gap_stages_a_targeted_challenger(
    tmp_path: Path,
) -> None:
    """A healthy average plus a remembered gap must still produce training.

    This is the production case in one test: the recorded tape is profitable, so
    the old diagnosis was ``stable_baseline`` and the run learned nothing while
    the panel showed five archetypes underwater. Now the gap survives to the
    next run, becomes a diagnosis, and stages a change aimed at it.
    """
    from test_promotion_trial import _cfg

    from spy_der.dojo.runner import run_dojo

    experience = _seed_profitable_tape(tmp_path / "experience")
    record_archetype_gaps(tmp_path, PANEL, report_date="2026-07-25")

    out = run_dojo(_cfg(tmp_path), experience=experience)
    learner = out["metrics"]["phases"]["learner"]
    targets = out["metrics"]["training_targets"]

    # The old behaviour: profitable tape → "stable_baseline" → nothing staged.
    assert "stable_baseline" not in learner["diagnoses"]
    assert "weak_archetype:crash" in learner["diagnoses"]
    assert [g["archetype"] for g in targets["remembered_gaps"]] == ["crash", "range_chop"]
    assert learner["outcome"] == "promotion_recommended"
    assert learner["staged_target_archetype"] == "crash"
    assert targets["targeted_archetype"] == "crash"
    assert learner["staged_changes"] == {"prefer_abstain_on_ood": True}

    # ...and the guardrail still holds: standing down costs real P&L on this
    # tape, so the change is tried and refused rather than promoted on a
    # synthetic argument.
    promotion = out["metrics"]["phases"]["promotion"]
    assert promotion["enacted"] is False
    assert promotion["blocking_gate"] == "pnl_edge"
