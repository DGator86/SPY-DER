"""Live Dojo progress file — what the dashboard square reads while a run works."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from spy_der.dojo.progress import (
    STALE_AFTER_SECONDS,
    DojoProgress,
    annotate_dojo_progress,
    idle_dojo_progress,
    read_dojo_progress,
)


def test_progress_publishes_phases_and_finishes(tmp_path: Path) -> None:
    progress = DojoProgress(
        reports_dir=tmp_path / "reports" / "dojo",
        state_root=tmp_path,
        report_date="2026-07-28",
    )
    progress.begin_phase("recorded", "Scoring stored market sessions")
    progress.update("Built 2026-07-27 — 12 packet(s)", phase="recorded")
    progress.finish_phase("recorded", detail="ok")
    progress.begin_phase("sequential", "Blind days")
    progress.finish_phase("sequential", detail="skipped", skipped=True)
    progress.finish(summary="recorded tape: ok · sequential: skipped")

    body = read_dojo_progress(tmp_path / "reports" / "dojo")
    assert body is not None
    assert body["status"] == "finished"
    assert body["detail"].startswith("recorded tape")
    assert body["live"] is False
    phases = {entry["name"]: entry for entry in body["phases"]}
    assert phases["recorded"]["status"] == "done"
    assert phases["sequential"]["status"] == "skipped"
    assert phases["learner"]["status"] == "skipped"  # never reached
    heartbeat = tmp_path / "health" / "dojo.json"
    assert heartbeat.is_file()


def test_fail_marks_current_phase(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="tape blew up"):
        with DojoProgress(
            reports_dir=tmp_path / "reports" / "dojo",
            state_root=tmp_path,
            report_date="2026-07-28",
        ) as progress:
            progress.begin_phase("learner", "Diagnosing")
            raise RuntimeError("tape blew up")

    body = read_dojo_progress(tmp_path / "reports" / "dojo")
    assert body is not None
    assert body["status"] == "failed"
    assert "RuntimeError" in body["detail"]
    phases = {entry["name"]: entry for entry in body["phases"]}
    assert phases["learner"]["status"] == "failed"


def test_stale_running_progress_is_annotated() -> None:
    stale_at = (datetime.now(tz=UTC) - timedelta(seconds=STALE_AFTER_SECONDS + 30)).isoformat()
    out = annotate_dojo_progress(
        {
            "status": "running",
            "phase": "recorded",
            "phase_label": "Real tape",
            "detail": "Building sessions",
            "updated_at": stale_at,
            "phases": [],
        }
    )
    assert out["status"] == "stale"
    assert out["live"] is False
    assert "no update" in out["detail"]


def test_idle_payload_has_pending_strip() -> None:
    idle = idle_dojo_progress()
    assert idle["status"] == "idle"
    assert idle["live"] is False
    assert [p["status"] for p in idle["phases"]] == ["pending"] * 5


def test_missing_progress_file_reads_as_none(tmp_path: Path) -> None:
    assert read_dojo_progress(tmp_path / "reports" / "dojo") is None
