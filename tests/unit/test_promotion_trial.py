"""Automatic promotion — the trial, the gates, and what it enacts.

The Dojo may now write champion.json without a human. These tests pin the
conditions under which it does, and (mostly) the conditions under which it
refuses: a promotion that fires on thin evidence, on a config that loses to the
incumbent, or twice in one afternoon is the failure mode worth catching.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from dojo_tape import seed_tape

from spy_der.contracts.integration import (
    MarketCandidateView,
)
from spy_der.decisions.champion import (
    champion_provenance,
    load_champion_knobs,
    reset_champion_cache,
)
from spy_der.decisions.knobs import OOD_VETO, DecisionKnobs, actionable_knobs
from spy_der.dojo.config import DojoConfig
from spy_der.dojo.runner import run_dojo
from spy_der.integrations.zerodte.recorded_feed import FileMarketExperienceProvider
from spy_der.learning.hypotheses import generate_hypotheses
from spy_der.learning.promotion import (
    PromotionError,
    auto_promote_pending,
    current_champion,
    promote_pending,
    rollback_champion,
    stage_pending_review,
)
from spy_der.learning.promotion_trial import PromotionThresholds, run_promotion_trial

# --------------------------------------------------------------------------- #
# Tape: half the snapshots are out-of-distribution and lose money             #
# --------------------------------------------------------------------------- #
SESSIONS = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23"]


def _candidate(cid: str = "c1") -> MarketCandidateView:
    return MarketCandidateView(
        candidate_id=cid,
        family="long_call",
        direction="bullish",
        maximum_loss=Decimal("1"),
        capital_required=Decimal("1"),
        geometry_hash=f"sha256:{cid}",
        expiration=date(2026, 7, 22),
        utility=1.0,
    )


def _seed_ood_tape(root: Path, *, ticks: int = 40) -> FileMarketExperienceProvider:
    """Tape where high-uncertainty ticks lose and calm ticks win.

    A champion that trades everything nets a loss; a challenger that stands down
    when ``forecast_uncertainty`` is high keeps only the winners. That is a real
    edge the trial can measure, rather than one asserted by a hypothesis.
    """
    return seed_tape(
        root,
        pnl_for_tick=lambda tick: -0.30 if tick % 2 == 0 else 0.10,
        uncertainty_for_tick=lambda tick: 0.9 if tick % 2 == 0 else 0.1,
        ticks=ticks,
    )


def _cfg(tmp_path: Path, **kwargs: Any) -> DojoConfig:
    return DojoConfig(
        reports_dir=str(tmp_path / "reports"),
        configs_dir=str(tmp_path / "configs"),
        report_date="2026-07-24",
        min_ticks=100,
        min_sessions=3,
        skip_universe=True,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _clear_champion_cache() -> Any:
    reset_champion_cache()
    yield
    reset_champion_cache()


# --------------------------------------------------------------------------- #
# Import graph                                                                #
# --------------------------------------------------------------------------- #
def test_learning_package_imports_on_its_own() -> None:
    """`python -c "from spy_der.learning.promotion import rollback_champion"`.

    spy_der.dojo's package __init__ imports the runner, which imports learning;
    a module-level dojo import from learning therefore made that command — the
    documented rollback path — die on a circular import depending only on which
    package the operator touched first.
    """
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from spy_der.learning.promotion import rollback_champion;"
            " from spy_der.learning import run_promotion_trial",
        ],
        check=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONPATH": "src"},
    )


# --------------------------------------------------------------------------- #
# Knobs                                                                       #
# --------------------------------------------------------------------------- #
def test_knobs_only_ever_reduce_exposure() -> None:
    knobs = DecisionKnobs.from_mapping(
        {"risk_max_size_scalar": 0.75, "min_confidence": 0.6, "prefer_abstain_on_ood": True}
    )
    # A cap, never a lift: a packet already below the knob keeps its own scalar.
    assert knobs.effective_risk(1.0) == pytest.approx(0.75)
    assert knobs.effective_risk(0.2) == pytest.approx(0.2)
    assert OOD_VETO in knobs.effective_hard_vetoes((), 0.9)
    assert OOD_VETO not in knobs.effective_hard_vetoes((), 0.1)
    assert knobs.apply_confidence_floor("TRADE", "c1", 0.5) == ("ABSTAIN", None)
    assert knobs.apply_confidence_floor("TRADE", "c1", 0.9) == ("TRADE", "c1")


def test_knobs_ignore_junk_and_report_noop() -> None:
    assert DecisionKnobs.from_mapping({"note": "hold_champion"}).is_noop is True
    assert DecisionKnobs.from_mapping(None).is_noop is True
    assert DecisionKnobs.from_mapping({"min_confidence": "not-a-number"}).is_noop is True
    assert actionable_knobs({"note": "hold", "min_confidence": 0.6}) == {
        "min_confidence": 0.6
    }


def test_hypotheses_are_single_knob_and_testable() -> None:
    """Each candidate must be attributable — the trial scores one change."""
    for hypothesis in generate_hypotheses(["negative_pnl", "low_win_rate"]):
        knobs = actionable_knobs(hypothesis.change)
        assert len(knobs) <= 1, hypothesis.hypothesis_id
    ids = [h.hypothesis_id for h in generate_hypotheses(["negative_pnl"])]
    assert ids == ["h-0-negative_pnl-ood_abstain", "h-0-negative_pnl-confidence_floor"]


# --------------------------------------------------------------------------- #
# The trial                                                                   #
# --------------------------------------------------------------------------- #
def test_trial_validates_a_change_that_beats_the_champion(tmp_path: Path) -> None:
    experience = _seed_ood_tape(tmp_path / "experience")
    trial = run_promotion_trial(
        _cfg(tmp_path),
        changes={"prefer_abstain_on_ood": True},
        candidate_id="dojo-test-ood",
        experience=experience,
    )
    assert trial.status == "validated", trial.note
    assert trial.blocking_gate is None
    # It won by dropping the losing half of the tape, not by trading more.
    assert trial.candidate["total_pnl"] > trial.incumbent["total_pnl"]
    assert trial.candidate["trades"] < trial.incumbent["trades"]
    assert {g.name for g in trial.gates} == {
        "actionable",
        "evidence",
        "pnl_edge",
        "win_rate",
        "forward_transfer",
        "retention",
        "universe",
        "archetype_repair",
        "cooldown",
    }


def test_trial_rejects_a_change_with_no_edge(tmp_path: Path) -> None:
    experience = _seed_ood_tape(tmp_path / "experience")
    # Standing down when uncertainty is high is what wins on this tape; a knob
    # that never fires (threshold above every tick) can only tie.
    trial = run_promotion_trial(
        _cfg(tmp_path),
        changes={"prefer_abstain_on_ood": True, "ood_threshold": 5.0},
        candidate_id="dojo-test-noedge",
        experience=experience,
    )
    assert trial.status == "rejected"
    assert trial.blocking_gate == "pnl_edge"


def test_trial_rejects_thin_evidence(tmp_path: Path) -> None:
    experience = _seed_ood_tape(tmp_path / "experience")
    trial = run_promotion_trial(
        _cfg(tmp_path),
        changes={"prefer_abstain_on_ood": True},
        candidate_id="dojo-test-thin",
        experience=experience,
        thresholds=PromotionThresholds(min_trades=10_000),
    )
    assert trial.status == "rejected"
    assert trial.blocking_gate == "evidence"


def test_trial_refuses_a_hold_champion_hypothesis(tmp_path: Path) -> None:
    trial = run_promotion_trial(
        _cfg(tmp_path),
        changes={"note": "hold_champion"},
        candidate_id="dojo-test-hold",
        experience=None,
    )
    assert trial.status == "not_actionable"
    assert trial.validated is False


def test_trial_respects_the_cooldown(tmp_path: Path) -> None:
    experience = _seed_ood_tape(tmp_path / "experience")
    just_now = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    trial = run_promotion_trial(
        _cfg(tmp_path),
        changes={"prefer_abstain_on_ood": True},
        candidate_id="dojo-test-cooldown",
        experience=experience,
        current_champion={"promoted_at": just_now},
    )
    assert trial.status == "rejected"
    assert trial.blocking_gate == "cooldown"


def test_trial_rejects_when_the_synthetic_panel_disagrees(tmp_path: Path) -> None:
    experience = _seed_ood_tape(tmp_path / "experience")
    trial = run_promotion_trial(
        _cfg(tmp_path),
        changes={"prefer_abstain_on_ood": True},
        candidate_id="dojo-test-universe",
        experience=experience,
        universe_result={
            "status": "ok",
            "n_scored_universes": 6,
            "authorities": {
                "champion": {"total_pnl": 10.0, "trades": 100, "n_universes": 6},
                "challenger": {"total_pnl": -4.0, "trades": 100, "n_universes": 6},
            },
        },
    )
    assert trial.status == "rejected"
    assert trial.blocking_gate == "universe"


# --------------------------------------------------------------------------- #
# Writing champion.json                                                       #
# --------------------------------------------------------------------------- #
def _stage(configs: Path, candidate_id: str = "cand-1") -> None:
    stage_pending_review(
        configs,
        candidate_id=candidate_id,
        payload={"knobs": {"prefer_abstain_on_ood": True}},
        auto_promote=True,
    )


def test_auto_promote_requires_a_validated_report(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    _stage(configs)
    for bad in (
        {},
        {"status": "rejected", "gates": [{"name": "pnl_edge", "passed": False}]},
        {"status": "validated"},
        {"status": "validated", "gates": []},
        {
            "status": "validated",
            "gates": [{"name": "pnl_edge", "passed": False}],
        },
    ):
        with pytest.raises(PromotionError):
            auto_promote_pending(configs, "cand-1", validation=bad)
    assert (configs / "champion.json").exists() is False


def test_auto_promote_writes_champion_and_keeps_rollback(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    _stage(configs, "cand-1")
    validation = {"status": "validated", "gates": [{"name": "pnl_edge", "passed": True}]}
    champion = auto_promote_pending(
        configs, "cand-1", validation=validation, knobs={"prefer_abstain_on_ood": True}
    )
    payload = json.loads(champion.read_text(encoding="utf-8"))
    assert payload["status"] == "champion"
    assert payload["promoted_by"] == "dojo_auto"
    assert payload["knobs"] == {"prefer_abstain_on_ood": True}
    assert payload["promoted_at"]
    # Staged file is retired, not left to be promoted twice.
    assert (configs / "pending_review" / "cand-1.json").exists() is False
    assert (configs / "promoted" / "cand-1.json").is_file()

    # A second promotion archives the first, and rollback puts it back.
    _stage(configs, "cand-2")
    auto_promote_pending(configs, "cand-2", validation=validation, knobs={"min_confidence": 0.6})
    assert current_champion(configs)["candidate_id"] == "cand-2"
    restored = rollback_champion(configs)
    assert restored is not None
    assert current_champion(configs)["candidate_id"] == "cand-1"


def test_human_promotion_path_still_requires_ack(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    _stage(configs)
    with pytest.raises(PromotionError):
        promote_pending(configs, "cand-1", human_ack="yes")
    champion = promote_pending(configs, "cand-1", human_ack="PROMOTE")
    payload = json.loads(champion.read_text(encoding="utf-8"))
    assert payload["promoted_by"] == "human"


# --------------------------------------------------------------------------- #
# What a promotion changes downstream                                         #
# --------------------------------------------------------------------------- #
def test_promoted_champion_is_read_by_the_live_decision_path(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    assert load_champion_knobs(configs).is_noop is True
    _stage(configs)
    auto_promote_pending(
        configs,
        "cand-1",
        validation={"status": "validated", "gates": [{"name": "pnl_edge", "passed": True}]},
        knobs={"prefer_abstain_on_ood": True, "min_confidence": 0.6},
    )
    reset_champion_cache()
    knobs = load_champion_knobs(configs)
    assert knobs.prefer_abstain_on_ood is True
    assert knobs.min_confidence == pytest.approx(0.6)
    assert champion_provenance(configs)["promoted_by"] == "dojo_auto"


def test_champion_knobs_kill_switch(tmp_path: Path, monkeypatch: Any) -> None:
    configs = tmp_path / "configs"
    _stage(configs)
    auto_promote_pending(
        configs,
        "cand-1",
        validation={"status": "validated", "gates": [{"name": "pnl_edge", "passed": True}]},
        knobs={"min_confidence": 0.6},
    )
    reset_champion_cache()
    monkeypatch.setenv("SPY_DER_CHAMPION_KNOBS", "0")
    assert load_champion_knobs(configs).is_noop is True


def test_malformed_champion_never_breaks_the_decision_path(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir(parents=True)
    (configs / "champion.json").write_text("{not json", encoding="utf-8")
    reset_champion_cache()
    assert load_champion_knobs(configs).is_noop is True


# --------------------------------------------------------------------------- #
# End to end through run_dojo                                                 #
# --------------------------------------------------------------------------- #
def test_run_dojo_promotes_without_a_human(tmp_path: Path) -> None:
    experience = _seed_ood_tape(tmp_path / "experience")
    cfg = _cfg(tmp_path)
    out = run_dojo(cfg, experience=experience)

    learner = out["metrics"]["phases"]["learner"]
    promotion = out["metrics"]["phases"]["promotion"]
    assert learner["outcome"] == "promotion_recommended"
    assert promotion["status"] == "validated", promotion["note"]
    assert promotion["enacted"] is True
    assert any(f["flag"] == "champion_promoted" for f in out["flags"])
    assert "Learner promoted a safer setting" in out["summary"]

    champion = current_champion(tmp_path / "configs")
    assert champion is not None
    assert champion["promoted_by"] == "dojo_auto"
    assert champion["knobs"] == {"prefer_abstain_on_ood": True}
    # The evidence that justified it travels with the config.
    assert champion["validation"]["status"] == "validated"
    assert all(g["passed"] for g in champion["validation"]["gates"])


def test_run_dojo_does_not_promote_twice_in_the_cooldown(tmp_path: Path) -> None:
    experience = _seed_ood_tape(tmp_path / "experience")
    run_dojo(_cfg(tmp_path), experience=experience)
    first = current_champion(tmp_path / "configs")

    reset_champion_cache()
    out = run_dojo(_cfg(tmp_path), experience=experience)
    promotion = out["metrics"]["phases"]["promotion"]
    assert promotion["enacted"] is False
    # Three refusals are all correct here: the champion now carries the knobs,
    # so the tape no longer diagnoses a problem (no_candidate); or a candidate
    # is staged but ties the incumbent (pnl_edge); or the cooldown holds.
    assert promotion["status"] in {"no_candidate", "rejected"}
    if promotion["status"] == "rejected":
        assert promotion["blocking_gate"] in {"cooldown", "pnl_edge"}
    assert current_champion(tmp_path / "configs")["promoted_at"] == first["promoted_at"]


def test_run_dojo_auto_promote_can_be_switched_off(tmp_path: Path) -> None:
    experience = _seed_ood_tape(tmp_path / "experience")
    cfg = _cfg(tmp_path)
    cfg.auto_promote = False
    out = run_dojo(cfg, experience=experience)
    promotion = out["metrics"]["phases"]["promotion"]
    assert promotion["status"] == "disabled"
    assert (tmp_path / "configs" / "champion.json").exists() is False
    assert any(f["flag"] == "promotion_untried" for f in out["flags"])
