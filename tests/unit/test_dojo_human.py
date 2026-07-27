"""Plain-English Dojo report copy."""

from __future__ import annotations

from spy_der.dojo.human import build_human_report, humanize_flag
from spy_der.dojo.reports import persist_dojo_report, read_latest_dojo_report


def test_humanize_flag_weak_archetype() -> None:
    assert humanize_flag("weak_archetype:crash") == "weak on crash"
    assert humanize_flag("champion_promoted") == "safer setting promoted"


def test_build_human_report_answers_data_and_stop() -> None:
    human = build_human_report(
        recorded={"status": "ok", "n_sessions": 8, "evaluation": {"trades": 10, "total_pnl": 1.5}},
        sequential={"status": "ok"},
        learner={"outcome": "no_change"},
        universe={
            "status": "ok",
            "n_universes": 16,
            "remediation": {
                "headline": "Next sparring will focus on crash, range chop.",
                "focus": [
                    {
                        "archetype": "crash",
                        "label": "crash",
                        "reasons": ["negative mean P&L"],
                    }
                ],
            },
        },
        promotion={"status": "no_candidate"},
        flags=[{"flag": "weak_archetype:crash", "severity": "warn"}],
        summary="Real tape OK · Sparring: 16 worlds",
        config={"generations": 2, "universes_per_gen": 8},
    )
    assert "stored real market sessions" in human["data_story"].lower()
    assert "did not trade" in human["data_story"].lower()
    assert "fixed budget" in human["stop_reason"].lower()
    assert "crash" in human["next_step"].lower()
    assert human["flag_labels"] == ["weak on crash"]
    assert human["phases"]["recorded"]["title"] == "Real market tape"


def test_persist_dojo_report_includes_human(tmp_path) -> None:
    human = {"headline": "Gaps found", "data_story": "Stored + synthetic."}
    persist_dojo_report(
        tmp_path,
        report_date="2026-07-26",
        summary="Real tape OK",
        flags=[],
        metrics={"phases": {}},
        human=human,
    )
    latest = read_latest_dojo_report(tmp_path)
    assert latest is not None
    assert latest["human"]["headline"] == "Gaps found"
