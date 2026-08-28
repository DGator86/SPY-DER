"""Dojo promotion trials may discover edge, but authority remains human-gated."""

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

from spy_der.contracts.integration import MarketCandidateView
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
    """Tape where high-uncertainty ticks lose and calm ticks win."""
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


def test_learning_package_imports_on_its_own() -> None:
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


def test_knobs_only_ever_reduce_exposure() -> None:
    knobs = DecisionKnobs.from_mapping(
        {"risk_max_size_scalar": 0.75, "min_confidence": 0.6, "prefer_abstain_on_ood": True}
    )
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
    for hypothesis in generate_hypotheses(["negative_pnl", "low_win_rate"]):
        knobs = actionable_knobs(hypothesis.change)
        assert len(knobs) <= 1, hypothesis.hypothesis_id
    ids = [h.hypothesis_id for h in generate_hypotheses(["negative_pnl"])]
    assert ids == ["h-0-negative_pnl-ood_abstain", "h-0-negative_pnl-confidence_floor"]


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


def _stage(configs: Path, candidate_id: str = "cand-1", **knobs: Any) -> None:
    stage_pending_review(
        configs,
        candidate_id=candidate_id,
        payload={"knobs": knobs or {"prefer_abstain_on_ood": True}},
        auto_promote=True,
    )


def test_automatic_champion_promotion_is_hard_disabled(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    _stage(configs)
    validation = {"status": "validated", "gates": [{"name": "pnl_edge", "passed": True}]}
    with pytest.raises(PromotionError, match="automatic champion promotion is disabled"):
        auto_promote_pending(configs, "cand-1", validation=validation)
    assert (configs / "champion.json").exists() is False
    assert (configs / "pending_review" / "cand-1.json").is_file()


def test_human_promotion_requires_ack_and_is_reversible(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    _stage(configs, "cand-1")
    with pytest.raises(PromotionError):
        promote_pending(configs, "cand-1", human_ack="yes")
    first = promote_pending(configs, "cand-1", human_ack="PROMOTE")
    first_payload = json.loads(first.read_text(encoding="utf-8"))
    assert first_payload["promoted_by"] == "human"

    _stage(configs, "cand-2", min_confidence=0.6)
    promote_pending(configs, "cand-2", human_ack="PROMOTE")
    assert current_champion(configs)["candidate_id"] == "cand-2"
    restored = rollback_champion(configs)
    assert restored is not None
    assert current_champion(configs)["candidate_id"] == "cand-1"


def test_human_promoted_champion_is_read_by_live_decision_path(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    assert load_champion_knobs(configs).is_noop is True
    _stage(configs, prefer_abstain_on_ood=True, min_confidence=0.6)
    promote_pending(configs, "cand-1", human_ack="PROMOTE")
    reset_champion_cache()
    knobs = load_champion_knobs(configs)
    assert knobs.prefer_abstain_on_ood is True
    assert knobs.min_confidence == pytest.approx(0.6)
    assert champion_provenance(configs)["promoted_by"] == "human"


def test_champion_knobs_kill_switch(tmp_path: Path, monkeypatch: Any) -> None:
    configs = tmp_path / "configs"
    _stage(configs, min_confidence=0.6)
    promote_pending(configs, "cand-1", human_ack="PROMOTE")
    reset_champion_cache()
    monkeypatch.setenv("SPY_DER_CHAMPION_KNOBS", "0")
    assert load_champion_knobs(configs).is_noop is True


def test_malformed_champion_never_breaks_the_decision_path(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir(parents=True)
    (configs / "champion.json").write_text("{not json", encoding="utf-8")
    reset_champion_cache()
    assert load_champion_knobs(configs).is_noop is True


def test_run_dojo_cannot_self_promote_a_validated_trial(tmp_path: Path) -> None:
    experience = _seed_ood_tape(tmp_path / "experience")
    out = run_dojo(_cfg(tmp_path), experience=experience)
    learner = out["metrics"]["phases"]["learner"]
    promotion = out["metrics"]["phases"]["promotion"]
    assert learner["outcome"] == "promotion_recommended"
    # The compatibility guard is surfaced by the old runner as a write failure;
    # critically, authority is unchanged and no champion file is created.
    assert promotion["status"] == "promotion_failed"
    assert promotion["enacted"] is False
    assert (tmp_path / "configs" / "champion.json").exists() is False
    assert any(f["flag"] == "promotion_write_failed" for f in out["flags"])


def test_run_dojo_auto_promote_can_be_switched_off(tmp_path: Path) -> None:
    experience = _seed_ood_tape(tmp_path / "experience")
    cfg = _cfg(tmp_path)
    cfg.auto_promote = False
    out = run_dojo(cfg, experience=experience)
    promotion = out["metrics"]["phases"]["promotion"]
    assert promotion["status"] == "disabled"
    assert (tmp_path / "configs" / "champion.json").exists() is False
    assert any(f["flag"] == "promotion_untried" for f in out["flags"])
