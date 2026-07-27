"""Training the gaps — the robustness matrix has to change what the Dojo does.

The universe panel could always say "crash is underwater". These tests pin the
three hops that were missing between saying it and doing something about it:
the lattice spends its next generation there, the learner diagnoses it, and a
change staged to repair it is held to that archetype before it can promote.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from dojo_tape import seed_tape

from spy_der.dojo.config import DojoConfig
from spy_der.learning.gaps import (
    ArchetypeGap,
    load_archetype_gaps,
    record_archetype_gaps,
    weakest_archetypes,
)
from spy_der.learning.hypotheses import (
    diagnose,
    generate_hypotheses,
    target_archetype_of,
)
from spy_der.learning.memories import append_failure_episode
from spy_der.learning.promotion_trial import MIN_TARGET_TRADES, run_promotion_trial

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
    # Rewrite the stored timestamp to make it old. Editing the field rather
    # than swapping a date substring keeps the test correct across UTC midnight.
    store = tmp_path / "memories" / "failure_episodes.json"
    records = json.loads(store.read_text(encoding="utf-8"))
    for record in records:
        record["created_at"] = old
    store.write_text(json.dumps(records), encoding="utf-8")
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
    record_archetype_gaps(tmp_path, repaired)
    # The store is what the next run reads — a recovered archetype must leave
    # it, not linger for 14 days steering diagnoses at a problem already fixed.
    assert "crash" not in {g.archetype for g in load_archetype_gaps(tmp_path)}
    assert "range_chop" in {g.archetype for g in load_archetype_gaps(tmp_path)}


def test_a_thin_re_score_does_not_clear_a_gap(tmp_path: Path) -> None:
    """Recovery needs the same evidence floor that opened the gap."""
    record_archetype_gaps(tmp_path, PANEL)
    thin_positive = {
        "status": "ok",
        "archetype_matrix": {
            "crash": {
                "mean_session_pnl": 5.0, "total_pnl": 5.0,
                "n_sessions": 1, "n_universes": 1,
            },
        },
    }
    record_archetype_gaps(tmp_path, thin_positive)
    assert "crash" in {g.archetype for g in load_archetype_gaps(tmp_path)}


# --------------------------------------------------------------------------- #
# Spending the lattice where the gaps are                                     #
# --------------------------------------------------------------------------- #
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
    plans = universe["generation_plans"]
    assert plans, "no re-weighting was applied between generations"
    # Weights are not all equal — some archetype earned more draws than others.
    weights = plans[0]["weights"]
    assert len(set(round(float(v), 4) for v in weights.values())) > 1


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
    return seed_tape(
        root,
        pnl_for_tick=lambda _tick: 0.10,
        uncertainty_for_tick=lambda tick: 0.9 if tick % 2 == 0 else 0.1,
        ticks=ticks,
    )


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


def test_repair_gate_fails_closed_without_evidence(tmp_path: Path) -> None:
    """No panel, no visit, or too few trades must all refuse — not rubber-stamp.

    The daily timers skip the universe phase and lattice sampling is stochastic,
    so "crash was never drawn" is routine. Passing the gate on missing evidence
    would wave through exactly the change it exists to check.
    """
    from test_promotion_trial import _cfg

    experience = _seed_profitable_tape(tmp_path / "experience")
    cases = {
        "no panel at all": {"status": "ok", "n_scored_universes": 6},
        "target never drawn": {
            "status": "ok",
            "archetype_authorities": {"calm_pin": {
                "champion": {"total_pnl": 1.0, "trades": 50},
                "challenger": {"total_pnl": 2.0, "trades": 50},
            }},
        },
        "target barely traded": {
            "status": "ok",
            "archetype_authorities": {"crash": {
                "champion": {"total_pnl": -100.0, "trades": 50},
                # Two lucky abstain-heavy ticks beat -100 on total P&L alone.
                "challenger": {"total_pnl": 0.01, "trades": 2},
            }},
        },
    }
    for label, panel in cases.items():
        trial = run_promotion_trial(
            _cfg(tmp_path),
            changes={"prefer_abstain_on_ood": True},
            candidate_id=f"dojo-{label.replace(' ', '-')}",
            experience=experience,
            universe_result=panel,
            target_archetype="crash",
        )
        repair = next(g for g in trial.gates if g.name == "archetype_repair")
        assert repair.passed is False, f"{label} passed the repair gate"
    assert MIN_TARGET_TRADES >= 10, "the target evidence floor must be meaningful"
