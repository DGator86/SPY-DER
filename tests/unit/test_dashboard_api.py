"""Read-only dashboard API — the surface that makes a Dojo report visible.

These are handler-level tests; no sockets are bound.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from spy_der.dojo.reports import persist_dojo_report
from spy_der.runtime.dashboard_api import (
    UI_ROOT,
    DashboardApiState,
    handle_get,
    read_ui_asset,
)
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


def test_dojo_progress_is_idle_before_any_run(tmp_path: Path) -> None:
    code, body = handle_get(_state(tmp_path), "/v1/dojo/progress")
    assert code == 200
    assert body["status"] == "idle"
    assert body["live"] is False
    assert len(body["phases"]) == 5


def test_dojo_progress_serves_a_live_run(tmp_path: Path) -> None:
    from spy_der.dojo.progress import DojoProgress

    progress = DojoProgress(
        reports_dir=tmp_path / "reports" / "dojo",
        state_root=tmp_path,
        report_date="2026-07-28",
    )
    progress.begin_phase("recorded", "Scoring stored market sessions")
    progress.update("Built 2026-07-27 — 12 packet(s)")

    code, body = handle_get(_state(tmp_path), "/v1/dojo/progress")
    assert code == 200
    assert body["status"] == "running"
    assert body["live"] is True
    assert "Built 2026-07-27" in body["detail"]
    assert body["phases"][0]["status"] == "running"


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
# Validation reports                                                          #
# --------------------------------------------------------------------------- #
def _write_validation(tmp_path: Path, *, ok: bool = True) -> None:
    directory = tmp_path / "reports" / "validation"
    directory.mkdir(parents=True, exist_ok=True)
    body = {
        "report_date": "2026-07-24",
        "summary": "window=recent days=5 · 3 pass, 0 fail, 13 pending",
        "ok": ok,
        "gates": [{"gate": "stale_feed", "verdict": "pass", "detail": "clean"}],
    }
    for name in ("validation_20260724_060000.json", "latest.json"):
        (directory / name).write_text(json.dumps(body), encoding="utf-8")


def test_latest_validation_report_is_served(tmp_path: Path) -> None:
    _write_validation(tmp_path)
    code, body = handle_get(_state(tmp_path), "/v1/validation/latest")
    assert code == 200
    assert body["ok"] is True
    assert body["gates"][0]["gate"] == "stale_feed"


def test_missing_validation_report_explains_itself(tmp_path: Path) -> None:
    code, body = handle_get(_state(tmp_path), "/v1/validation/latest")
    assert code == 404
    assert body["error"] == "no_validation_report"
    assert body["path"].endswith("reports/validation/latest.json")


def test_validation_index_carries_the_verdict(tmp_path: Path) -> None:
    """A validation report has gates and an `ok`, not dojo-style flags."""
    _write_validation(tmp_path, ok=False)
    code, body = handle_get(_state(tmp_path), "/v1/validation/reports")
    assert code == 200
    entry = body["reports"][0]
    assert entry["name"] == "validation_20260724_060000.json"
    assert entry["gates"] == 1
    assert entry["ok"] is False
    assert "flags" not in entry


def test_dojo_and_validation_indexes_do_not_bleed(tmp_path: Path) -> None:
    """Each index reads its own directory and prefix."""
    _write_report(tmp_path)
    _write_validation(tmp_path)
    _, dojo = handle_get(_state(tmp_path), "/v1/dojo/reports")
    _, validation = handle_get(_state(tmp_path), "/v1/validation/reports")
    assert all(r["name"].startswith("dojo_") for r in dojo["reports"])
    assert all(r["name"].startswith("validation_") for r in validation["reports"])


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


# --------------------------------------------------------------------------- #
# Attribution route                                                           #
# --------------------------------------------------------------------------- #
def test_attribution_latest_is_404_before_any_report(tmp_path: Path) -> None:
    code, body = handle_get(_state(tmp_path), "/v1/attribution/latest")
    assert code == 404
    assert body["error"] == "no_attribution_report"


def test_attribution_latest_serves_the_persisted_report(tmp_path: Path) -> None:
    from decimal import Decimal

    from spy_der.evaluation.attribution import (
        ActualTrade,
        PlannedTrade,
        attribute_session,
    )
    from spy_der.evaluation.reports import persist_attribution_report

    report = attribute_session(
        [
            (
                PlannedTrade(
                    candidate_id="cand-a",
                    contracts=10,
                    entry_price=Decimal("-0.50"),
                    exit_price=Decimal("0"),
                    session_date="2026-07-24",
                ),
                ActualTrade(
                    candidate_id="cand-a",
                    contracts=10,
                    entry_price=Decimal("-0.40"),
                    exit_price=Decimal("0"),
                ),
            )
        ]
    )
    persist_attribution_report(tmp_path / "reports" / "attribution", report=report)
    code, body = handle_get(_state(tmp_path), "/v1/attribution/latest")
    assert code == 200
    assert body["verdict"] == "execution_drag"
    assert body["report_date"] == "2026-07-24"


def test_attribution_report_index_is_empty_not_an_error(tmp_path: Path) -> None:
    code, body = handle_get(_state(tmp_path), "/v1/attribution/reports")
    assert code == 200
    assert body["reports"] == []


# --------------------------------------------------------------------------- #
# UI assets                                                                   #
# --------------------------------------------------------------------------- #
def test_ui_root_serves_the_standalone_shell() -> None:
    code, content_type, body = read_ui_asset("/ui")
    assert code == 200
    assert content_type.startswith("text/html")
    assert b"data-spy-der-tab" in body


def test_ui_serves_the_module_and_stylesheet() -> None:
    for name, expected in (("spy-der-tab.js", "javascript"), ("spy-der-tab.css", "css")):
        code, content_type, body = read_ui_asset(f"/ui/{name}")
        assert code == 200, name
        assert expected in content_type
        assert body


def test_ui_assets_ship_inside_the_installed_package() -> None:
    # The unit reads these out of site-packages on the VPS, not a checkout.
    assert UI_ROOT.is_dir()
    assert (UI_ROOT / "index.html").is_file()
    assert UI_ROOT.parent.name == "runtime"


def test_ui_rejects_path_traversal() -> None:
    for attempt in (
        "/ui/../../../etc/passwd",
        "/ui/..%2f..%2fetc%2fpasswd",
        "/ui/subdir/thing.js",
        "/ui/../dashboard_api.py",
    ):
        code, _, body = read_ui_asset(attempt)
        assert code == 404, attempt
        assert body == b"not found"


def test_ui_rejects_unlisted_suffixes() -> None:
    # Only html/css/js are servable; a .py in the same directory is not.
    code, _, _ = read_ui_asset("/ui/spy-der-tab.py")
    assert code == 404


def test_ui_is_not_reachable_through_the_json_handler(tmp_path: Path) -> None:
    # The MCP surface wraps handle_get; assets must stay off it.
    code, body = handle_get(_state(tmp_path), "/ui")
    assert code == 404
    assert body["error"] == "not_found"


# --------------------------------------------------------------------------- #
# Pending challengers + operator promote/reject                               #
# --------------------------------------------------------------------------- #
def test_pending_queue_is_empty_before_any_challenger(tmp_path: Path) -> None:
    code, body = handle_get(_state(tmp_path), "/v1/dojo/pending")
    assert code == 200
    assert body["pending"] == []
    assert body["count"] == 0
    assert body["lane"] == "decision_knobs"
    assert body["actions_enabled"] is False


def test_pending_queue_lists_staged_knob_challengers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from spy_der.learning.promotion import stage_pending_review
    from spy_der.runtime.dashboard_api import OPERATOR_TOKEN_ENV

    monkeypatch.setenv(OPERATOR_TOKEN_ENV, "secret-token")
    stage_pending_review(
        tmp_path / "configs",
        candidate_id="cand-ood",
        payload={
            "knobs": {"prefer_abstain_on_ood": True},
            "target_archetype": "chop",
            "mode": "remediate",
        },
        auto_promote=False,
    )
    code, body = handle_get(_state(tmp_path), "/v1/dojo/pending")
    assert code == 200
    assert body["count"] == 1
    assert body["actions_enabled"] is True
    assert body["pending"][0]["candidate_id"] == "cand-ood"
    assert body["pending"][0]["knobs"]["prefer_abstain_on_ood"] is True
    assert body["pending"][0]["target_archetype"] == "chop"


def test_promote_requires_operator_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from spy_der.learning.promotion import stage_pending_review
    from spy_der.runtime.dashboard_api import OPERATOR_TOKEN_ENV, handle_post

    monkeypatch.delenv(OPERATOR_TOKEN_ENV, raising=False)
    stage_pending_review(
        tmp_path / "configs",
        candidate_id="cand-1",
        payload={"knobs": {"min_confidence": 0.55}},
    )
    code, body = handle_post(
        _state(tmp_path),
        "/v1/dojo/promote",
        {"candidate_id": "cand-1", "human_ack": "PROMOTE"},
        authorization="Bearer anything",
    )
    assert code == 503
    assert body["error"] == "operator_token_unset"


def test_promote_rejects_bad_bearer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from spy_der.learning.promotion import stage_pending_review
    from spy_der.runtime.dashboard_api import OPERATOR_TOKEN_ENV, handle_post

    monkeypatch.setenv(OPERATOR_TOKEN_ENV, "correct-token")
    stage_pending_review(
        tmp_path / "configs",
        candidate_id="cand-1",
        payload={"knobs": {"min_confidence": 0.55}},
    )
    code, body = handle_post(
        _state(tmp_path),
        "/v1/dojo/promote",
        {"candidate_id": "cand-1", "human_ack": "PROMOTE"},
        authorization="Bearer wrong-token",
    )
    assert code == 401
    assert body["error"] == "unauthorized"


def test_promote_and_reject_mutate_configs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from spy_der.learning.promotion import current_champion, stage_pending_review
    from spy_der.runtime.dashboard_api import OPERATOR_TOKEN_ENV, handle_post

    monkeypatch.setenv(OPERATOR_TOKEN_ENV, "op-token")
    configs = tmp_path / "configs"
    stage_pending_review(
        configs,
        candidate_id="keep-me",
        payload={"knobs": {"prefer_abstain_on_ood": True}},
    )
    stage_pending_review(
        configs,
        candidate_id="drop-me",
        payload={"knobs": {"min_confidence": 0.7}},
    )

    code, body = handle_post(
        _state(tmp_path),
        "/v1/dojo/promote",
        {"candidate_id": "keep-me", "human_ack": "PROMOTE"},
        authorization="Bearer op-token",
    )
    assert code == 200
    assert body["ok"] is True
    assert body["action"] == "promote"
    assert current_champion(configs)["candidate_id"] == "keep-me"
    assert [c["candidate_id"] for c in body["pending"]] == ["drop-me"]

    code, body = handle_post(
        _state(tmp_path),
        "/v1/dojo/reject",
        {"candidate_id": "drop-me"},
        authorization="Bearer op-token",
    )
    assert code == 200
    assert body["pending"] == []
    assert (configs / "challengers" / "rejected_drop-me.json").is_file()


def test_rollback_restores_previous_champion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from spy_der.learning.promotion import (
        current_champion,
        promote_pending,
        stage_pending_review,
    )
    from spy_der.runtime.dashboard_api import OPERATOR_TOKEN_ENV, handle_post

    monkeypatch.setenv(OPERATOR_TOKEN_ENV, "op-token")
    configs = tmp_path / "configs"
    stage_pending_review(
        configs, candidate_id="first", payload={"knobs": {"min_confidence": 0.4}}
    )
    promote_pending(configs, "first", human_ack="PROMOTE")
    stage_pending_review(
        configs, candidate_id="second", payload={"knobs": {"min_confidence": 0.8}}
    )
    promote_pending(configs, "second", human_ack="PROMOTE")
    assert current_champion(configs)["candidate_id"] == "second"

    code, body = handle_post(
        _state(tmp_path),
        "/v1/dojo/rollback",
        {},
        authorization="Bearer op-token",
    )
    assert code == 200
    assert body["action"] == "rollback"
    assert current_champion(configs)["candidate_id"] == "first"


def test_ui_shell_enables_operator_actions() -> None:
    code, _, body = read_ui_asset("/ui")
    assert code == 200
    assert b"data-spy-der-actions" in body
    assert b"Adaptive Loop" in body
