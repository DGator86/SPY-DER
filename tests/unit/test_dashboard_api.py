"""Read-only dashboard API — the surface that makes a Dojo report visible.

These are handler-level tests; no sockets are bound.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from spy_der.dojo.reports import persist_dojo_report
from spy_der.runtime.dashboard_api import DashboardApiState, handle_get
from spy_der.util.files import atomic_write_json


def _state(tmp_path: Path) -> DashboardApiState:
    return DashboardApiState(tmp_path)


def _write_report(tmp_path: Path, *, report_date: str = "2026-07-24") -> dict[str, str]:
    return persist_dojo_report(
        tmp_path / "reports" / "dojo",
        report_date=report_date,
        summary=f"recorded tape: ok · sequential: ok ({report_date})",
        flags=[{"severity": "warn", "flag": "weak_archetype:chop", "detail": "-0.01"}],
        metrics={"phases": {"recorded": {"status": "ok"}}},
    )


# --------------------------------------------------------------------------- #
# Routing                                                                     #
# --------------------------------------------------------------------------- #
def test_health_reports_the_state_root(tmp_path: Path) -> None:
    code, body = handle_get(_state(tmp_path), "/health")
    assert code == 200
    assert body["status"] == "ok"
    assert body["state_root"] == str(tmp_path)


def test_unknown_route_is_404(tmp_path: Path) -> None:
    code, body = handle_get(_state(tmp_path), "/v1/nope")
    assert code == 404
    assert body["error"] == "not_found"


# --------------------------------------------------------------------------- #
# Dojo reports                                                                #
# --------------------------------------------------------------------------- #
def test_latest_dojo_report_is_served_after_a_run(tmp_path: Path) -> None:
    _write_report(tmp_path)
    code, body = handle_get(_state(tmp_path), "/v1/dojo/latest")
    assert code == 200
    assert body["report_date"] == "2026-07-24"
    assert body["flags"][0]["flag"] == "weak_archetype:chop"


def test_missing_dojo_report_explains_itself(tmp_path: Path) -> None:
    """A 404 here is the operator's first diagnostic — it must name the path."""
    code, body = handle_get(_state(tmp_path), "/v1/dojo/latest")
    assert code == 404
    assert body["error"] == "no_dojo_report"
    assert body["path"].endswith("reports/dojo/latest.json")


def test_report_index_is_newest_first(tmp_path: Path) -> None:
    reports = tmp_path / "reports" / "dojo"
    for stamp in ("20260722_060000", "20260724_060000", "20260723_060000"):
        (reports).mkdir(parents=True, exist_ok=True)
        (reports / f"dojo_{stamp}.json").write_text(
            json.dumps({"report_date": stamp[:8], "summary": stamp, "flags": []}),
            encoding="utf-8",
        )
    code, body = handle_get(_state(tmp_path), "/v1/dojo/reports")
    assert code == 200
    assert [r["name"] for r in body["reports"]] == [
        "dojo_20260724_060000.json",
        "dojo_20260723_060000.json",
        "dojo_20260722_060000.json",
    ]


def test_report_index_honours_limit(tmp_path: Path) -> None:
    _write_report(tmp_path)
    code, body = handle_get(_state(tmp_path), "/v1/dojo/reports", "limit=0")
    assert code == 200
    assert body["reports"] == []


def test_report_index_rejects_a_non_integer_limit(tmp_path: Path) -> None:
    code, body = handle_get(_state(tmp_path), "/v1/dojo/reports", "limit=abc")
    assert code == 400
    assert body["error"] == "invalid_limit"


def test_a_corrupt_report_does_not_break_the_index(tmp_path: Path) -> None:
    reports = tmp_path / "reports" / "dojo"
    reports.mkdir(parents=True)
    (reports / "dojo_20260724_060000.json").write_text("{ truncated", encoding="utf-8")
    code, body = handle_get(_state(tmp_path), "/v1/dojo/reports")
    assert code == 200
    assert body["reports"][0]["error"] == "JSONDecodeError"


def test_report_index_is_empty_before_any_run(tmp_path: Path) -> None:
    code, body = handle_get(_state(tmp_path), "/v1/dojo/reports")
    assert code == 200
    assert body["reports"] == []


# --------------------------------------------------------------------------- #
# Live state                                                                  #
# --------------------------------------------------------------------------- #
def test_live_state_is_served_when_present(tmp_path: Path) -> None:
    atomic_write_json(tmp_path / "live_state.json", {"schema_version": "spyder.dashboard.v1"})
    code, body = handle_get(_state(tmp_path), "/v1/state")
    assert code == 200
    assert body["schema_version"] == "spyder.dashboard.v1"


def test_missing_live_state_is_404(tmp_path: Path) -> None:
    code, body = handle_get(_state(tmp_path), "/v1/state")
    assert code == 404
    assert body["error"] == "no_live_state"


# --------------------------------------------------------------------------- #
# Published state must be readable by the consumers that were promised it     #
# --------------------------------------------------------------------------- #
def test_atomic_write_json_publishes_world_readable_files(tmp_path: Path) -> None:
    """`mkstemp` defaults to 0600; `os.replace` used to carry that to the target.

    The dashboard API and the 0DTE adapter read these files as other users, so
    an owner-read-only `latest.json` is invisible to every consumer.
    """
    target = atomic_write_json(tmp_path / "live_state.json", {"a": 1})
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode & stat.S_IRGRP, oct(mode)
    assert mode & stat.S_IROTH, oct(mode)


def test_dojo_latest_report_is_world_readable(tmp_path: Path) -> None:
    paths = _write_report(tmp_path)
    for key in ("json_path", "latest_path"):
        mode = stat.S_IMODE(os.stat(paths[key]).st_mode)
        assert mode & stat.S_IROTH, f"{key} is {oct(mode)}"


def test_atomic_write_json_respects_a_restrictive_umask(tmp_path: Path) -> None:
    """Publishing loosens the default 0600 — it must not override an operator umask."""
    previous = os.umask(0o077)
    try:
        target = atomic_write_json(tmp_path / "private.json", {"a": 1})
        mode = stat.S_IMODE(os.stat(target).st_mode)
        assert not mode & stat.S_IROTH, oct(mode)
    finally:
        os.umask(previous)
