"""`spy-der validate` — parity gates over recorded sessions.

The gates are `docs/CUTOVER_PLAN.md`. The load-bearing property under test is
that a gate which cannot run reports `pending` and never `pass`: a validation
report that green-lights an ungated quantity is worse than no report.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from spy_der.contracts.common import content_hash
from spy_der.runtime.validation import (
    _PARITY_GATES_NEEDING_BOTH_RUNTIMES,
    ValidationConfig,
    main,
    run_validation,
)

TODAY = dt.date(2026, 7, 25)


def _snapshot(
    snapshot_id: str,
    *,
    schema_version: str = "1.0.0",
    normalization_version: str = "1.0.0",
    feed_status: str = "LIVE",
    penalty: float = 0.0,
    missing: tuple[str, ...] = (),
    flags: tuple[str, ...] = (),
) -> dict:
    return {
        "snapshot_id": snapshot_id,
        "schema_version": schema_version,
        "normalization_version": normalization_version,
        "feed_observations": [{"component": "UNDERLYING", "status": feed_status}],
        "data_quality": {"is_healthy": penalty == 0.0, "penalty": penalty, "flags": list(flags)},
        "missing_components": list(missing),
    }


def _record(seq: int, snapshot: dict) -> dict:
    """Build a record the way `market_data.recording.build_record` does."""
    return {
        "seq": seq,
        "snapshot_id": snapshot["snapshot_id"],
        "schema_version": snapshot["schema_version"],
        "record_hash": content_hash(snapshot),
        "snapshot": snapshot,
    }


def _write_session(root: Path, session: dt.date, snapshots: list[dict]) -> Path:
    market = root / "market"
    market.mkdir(parents=True, exist_ok=True)
    path = market / f"{session.isoformat()}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for seq, snap in enumerate(snapshots):
            handle.write(json.dumps(_record(seq, snap), sort_keys=True))
            handle.write("\n")
    return path


def _cfg(tmp_path: Path, **kw) -> ValidationConfig:
    return ValidationConfig(
        state_root=str(tmp_path),
        reports_dir=str(tmp_path / "reports" / "validation"),
        **kw,
    )


def _gate(out: dict, name: str) -> dict:
    return next(g for g in out["gates"] if g["gate"] == name)


# --------------------------------------------------------------------------- #
# Report shape and persistence                                                #
# --------------------------------------------------------------------------- #
def test_report_is_written_stamped_and_latest(tmp_path: Path) -> None:
    _write_session(tmp_path, TODAY, [_snapshot("s1")])
    out = run_validation(_cfg(tmp_path), today=TODAY)
    assert Path(out["json_path"]).is_file()
    assert Path(out["latest_path"]).is_file()
    latest = json.loads(Path(out["latest_path"]).read_text(encoding="utf-8"))
    assert latest["report_date"] == TODAY.isoformat()
    assert latest["window"] == "recent"


def test_a_run_with_no_recordings_is_pending_not_pass(tmp_path: Path) -> None:
    """No tape must never look like a clean bill of health."""
    out = run_validation(_cfg(tmp_path), today=TODAY)
    assert _gate(out, "recorded_session_replay")["verdict"] == "pending"
    assert _gate(out, "raw_market_snapshots")["verdict"] == "pending"
    assert _gate(out, "stale_feed")["verdict"] == "pending"
    assert not any(g["verdict"] == "pass" for g in out["gates"])
    assert out["ok"] is True  # pending is not failure


# --------------------------------------------------------------------------- #
# recorded_session_replay                                                     #
# --------------------------------------------------------------------------- #
def test_clean_recordings_pass_replay(tmp_path: Path) -> None:
    _write_session(tmp_path, TODAY, [_snapshot("s1"), _snapshot("s2")])
    out = run_validation(_cfg(tmp_path), today=TODAY)
    gate = _gate(out, "recorded_session_replay")
    assert gate["verdict"] == "pass"
    assert gate["metrics"]["snapshots"] == 2


def test_a_tampered_record_fails_replay(tmp_path: Path) -> None:
    path = _write_session(tmp_path, TODAY, [_snapshot("s1")])
    record = json.loads(path.read_text(encoding="utf-8").strip())
    record["snapshot"]["underlying_price"] = "999.99"  # hash no longer matches
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    out = run_validation(_cfg(tmp_path), today=TODAY)
    gate = _gate(out, "recorded_session_replay")
    assert gate["verdict"] == "fail"
    assert gate["metrics"]["corrupt"]
    assert out["ok"] is False


# --------------------------------------------------------------------------- #
# raw_market_snapshots                                                        #
# --------------------------------------------------------------------------- #
def test_mixed_normalization_versions_fail(tmp_path: Path) -> None:
    """Snapshots either side of a normalization change are not comparable."""
    _write_session(
        tmp_path,
        TODAY,
        [_snapshot("s1"), _snapshot("s2", normalization_version="1.1.0")],
    )
    out = run_validation(_cfg(tmp_path), today=TODAY)
    assert _gate(out, "raw_market_snapshots")["verdict"] == "fail"
    assert out["ok"] is False


# --------------------------------------------------------------------------- #
# stale_feed — degradation must propagate                                     #
# --------------------------------------------------------------------------- #
def test_degraded_feed_that_propagated_passes(tmp_path: Path) -> None:
    _write_session(
        tmp_path,
        TODAY,
        [_snapshot("s1", feed_status="STALE", penalty=0.4, missing=("option_chain",))],
    )
    out = run_validation(_cfg(tmp_path), today=TODAY)
    gate = _gate(out, "stale_feed")
    assert gate["verdict"] == "pass"
    assert gate["metrics"]["degraded_snapshots"] == 1


def test_degraded_feed_laundered_into_a_clean_snapshot_fails(tmp_path: Path) -> None:
    """A STALE component with no penalty and no missing-component record."""
    _write_session(tmp_path, TODAY, [_snapshot("s1", feed_status="STALE")])
    out = run_validation(_cfg(tmp_path), today=TODAY)
    gate = _gate(out, "stale_feed")
    assert gate["verdict"] == "fail"
    assert gate["metrics"]["laundered_snapshot_ids"] == ["s1"]
    assert out["ok"] is False


def test_missing_and_invalid_count_as_degraded(tmp_path: Path) -> None:
    _write_session(
        tmp_path,
        TODAY,
        [_snapshot("s1", feed_status="MISSING"), _snapshot("s2", feed_status="INVALID")],
    )
    gate = _gate(run_validation(_cfg(tmp_path), today=TODAY), "stale_feed")
    assert gate["metrics"]["degraded_snapshots"] == 2


def test_delayed_is_not_treated_as_degraded(tmp_path: Path) -> None:
    """DELAYED is a documented non-fatal state; only STALE/MISSING/INVALID fail closed."""
    _write_session(tmp_path, TODAY, [_snapshot("s1", feed_status="DELAYED")])
    gate = _gate(run_validation(_cfg(tmp_path), today=TODAY), "stale_feed")
    assert gate["verdict"] == "pass"
    assert gate["metrics"]["degraded_snapshots"] == 0


# --------------------------------------------------------------------------- #
# Gates that need both runtimes are pending, never pass                       #
# --------------------------------------------------------------------------- #
def test_cross_runtime_parity_gates_are_always_pending(tmp_path: Path) -> None:
    _write_session(tmp_path, TODAY, [_snapshot("s1")])
    out = run_validation(_cfg(tmp_path), today=TODAY)
    for name in _PARITY_GATES_NEEDING_BOTH_RUNTIMES:
        gate = _gate(out, name)
        assert gate["verdict"] == "pending", name
        assert "parallel 0DTE run" in gate["detail"], name


def test_every_cutover_tolerance_row_has_a_gate(tmp_path: Path) -> None:
    """The report must account for each quantity the cutover plan lists."""
    _write_session(tmp_path, TODAY, [_snapshot("s1")])
    out = run_validation(_cfg(tmp_path), today=TODAY)
    names = {g["gate"] for g in out["gates"]}
    for expected in (
        "candidate_ids",
        "candidate_geometry_hash",
        "maximum_loss",
        "capital_required",
        "hard_vetoes",
        "position_sizing",
        "size_scalar",
        "settlement",
        "forecast_probabilities",
        "forecast_cone_bounds",
        "feature_values",
        "regime_labels",
        "raw_market_snapshots",
        "journal_output",
    ):
        assert expected in names, expected


# --------------------------------------------------------------------------- #
# Windowing                                                                   #
# --------------------------------------------------------------------------- #
def test_recent_window_excludes_sessions_outside_it(tmp_path: Path) -> None:
    _write_session(tmp_path, TODAY, [_snapshot("today")])
    _write_session(tmp_path, TODAY - dt.timedelta(days=40), [_snapshot("old")])
    out = run_validation(_cfg(tmp_path, window="recent", days=5), today=TODAY)
    assert out["sessions"] == [TODAY.isoformat()]


def test_full_window_includes_everything(tmp_path: Path) -> None:
    _write_session(tmp_path, TODAY, [_snapshot("today")])
    _write_session(tmp_path, TODAY - dt.timedelta(days=40), [_snapshot("old")])
    out = run_validation(_cfg(tmp_path, window="full", days=60), today=TODAY)
    assert len(out["sessions"]) == 2


def test_a_non_date_filename_is_ignored(tmp_path: Path) -> None:
    _write_session(tmp_path, TODAY, [_snapshot("s1")])
    (tmp_path / "market" / "scratch.jsonl").write_text("{}\n", encoding="utf-8")
    out = run_validation(_cfg(tmp_path, window="full"), today=TODAY)
    assert out["sessions"] == [TODAY.isoformat()]


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def test_main_exits_zero_when_no_gate_fails(tmp_path: Path) -> None:
    _write_session(tmp_path, TODAY, [_snapshot("s1")])
    code = main(
        [
            "--state-root", str(tmp_path),
            "--reports-dir", str(tmp_path / "reports" / "validation"),
            "--report-date", TODAY.isoformat(),
        ]
    )
    assert code == 0


def test_main_exits_nonzero_on_a_failing_gate(tmp_path: Path) -> None:
    """A failing gate must fail the unit, not hide in JSON under a green status."""
    _write_session(tmp_path, TODAY, [_snapshot("s1", feed_status="STALE")])
    code = main(
        [
            "--state-root", str(tmp_path),
            "--reports-dir", str(tmp_path / "reports" / "validation"),
            "--report-date", TODAY.isoformat(),
        ]
    )
    assert code == 1


def test_main_accepts_the_units_config_flag(tmp_path: Path) -> None:
    """Both validation units pass --config; it must not be a hard error."""
    assert (
        main(
            [
                "--config", "/etc/spy-der/config.yaml",
                "--state-root", str(tmp_path),
                "--reports-dir", str(tmp_path / "reports" / "validation"),
                "--window", "full",
                "--days", "60",
            ]
        )
        == 0
    )
