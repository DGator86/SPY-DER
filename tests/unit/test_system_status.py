"""`/v1/system` — the view that replaces an SSH session.

Every assertion here stands for a question an operator would otherwise answer
with `systemctl status`, `journalctl`, `ls -l` or `git rev-parse`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from spy_der.runtime.heartbeat import classify_age, read_heartbeats, write_heartbeat
from spy_der.runtime.system_status import build_system_status

NOW = datetime(2026, 7, 22, 16, 0, tzinfo=UTC)  # 12:00 ET, session open


@pytest.fixture(autouse=True)
def _neutral_ai_env(monkeypatch: pytest.MonkeyPatch):
    """The AI section reads process env, which other modules set and leave set.

    `test_decision_service.py` sets SPY_DER_AI=0 in a `setup_function` and never
    restores it, so the gate reads `killswitch` here depending on test order.
    These assertions are about the market-hours gate, so the killswitch is
    cleared explicitly rather than inherited.
    """
    for name in ("SPY_DER_AI", "XAI_ENABLED", "SPY_DER_AI_MARKET_HOURS_ONLY"):
        monkeypatch.delenv(name, raising=False)
    yield


def _status(root: Path, **kw):
    return build_system_status(root, now=kw.pop("now", NOW))


# --------------------------------------------------------------------------- #
# Heartbeats                                                                  #
# --------------------------------------------------------------------------- #
def test_heartbeat_round_trips(tmp_path: Path) -> None:
    write_heartbeat(tmp_path, "market", interval_seconds=60, detail="390 ticks", now=NOW)
    entries = read_heartbeats(tmp_path, now=NOW)
    assert len(entries) == 1
    assert entries[0]["service"] == "market"
    assert entries[0]["detail"] == "390 ticks"
    assert entries[0]["state"] == "ok"


def test_heartbeat_is_world_readable(tmp_path: Path) -> None:
    """The dashboard adapter reads this as another user."""
    import os
    import stat

    path = write_heartbeat(tmp_path, "market", interval_seconds=60, now=NOW)
    assert stat.S_IMODE(os.stat(path).st_mode) & stat.S_IROTH


def test_heartbeat_failure_never_raises(tmp_path: Path, monkeypatch) -> None:
    """A full disk must degrade the dashboard, not stop the market feed."""
    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("spy_der.runtime.heartbeat.atomic_write_json", boom)
    write_heartbeat(tmp_path, "market", interval_seconds=60, now=NOW)  # must not raise


def test_age_classification_uses_the_services_own_interval() -> None:
    assert classify_age(30, 60) == "ok"
    assert classify_age(90, 60) == "late"     # within 3x
    assert classify_age(200, 60) == "stale"   # beyond 3x
    assert classify_age(None, 60) == "unknown"


def test_unreadable_heartbeat_is_unknown_not_missing(tmp_path: Path) -> None:
    (tmp_path / "health").mkdir()
    (tmp_path / "health" / "market.json").write_text("{ truncated", encoding="utf-8")
    entry = read_heartbeats(tmp_path, now=NOW)[0]
    assert entry["state"] == "unknown"
    assert "unreadable" in entry["note"]


# --------------------------------------------------------------------------- #
# Services section                                                            #
# --------------------------------------------------------------------------- #
def test_a_service_that_never_ran_is_reported_not_omitted(tmp_path: Path) -> None:
    """Silence about a service that should exist is the failure being hidden."""
    names = {s["service"]: s for s in _status(tmp_path)["services"]}
    assert set(names) >= {"market", "engine", "settlement"}
    assert names["market"]["state"] == "never_seen"


def test_each_service_carries_its_purpose(tmp_path: Path) -> None:
    names = {s["service"]: s for s in _status(tmp_path)["services"]}
    assert "market/" in names["market"]["purpose"]


def test_stale_service_is_surfaced(tmp_path: Path) -> None:
    write_heartbeat(
        tmp_path, "settlement", interval_seconds=300, now=NOW - timedelta(hours=2)
    )
    names = {s["service"]: s for s in _status(tmp_path)["services"]}
    assert names["settlement"]["state"] == "stale"


def test_an_unexpected_service_is_still_reported(tmp_path: Path) -> None:
    write_heartbeat(tmp_path, "experimental", interval_seconds=60, now=NOW)
    assert "experimental" in {s["service"] for s in _status(tmp_path)["services"]}


# --------------------------------------------------------------------------- #
# Overall banner — worst-of                                                   #
# --------------------------------------------------------------------------- #
def _healthy(root: Path) -> None:
    for name, interval in (("market", 60), ("engine", 30), ("settlement", 300)):
        write_heartbeat(root, name, interval_seconds=interval, now=NOW)
    market = root / "market"
    market.mkdir(parents=True, exist_ok=True)
    (market / "2026-07-22.jsonl").write_text(
        json.dumps({"snapshot": {"timestamp": NOW.isoformat()}}) + "\n", encoding="utf-8"
    )


def test_everything_healthy_is_ok(tmp_path: Path) -> None:
    _healthy(tmp_path)
    assert _status(tmp_path)["overall"] == "ok"


def test_one_stale_service_degrades_the_banner(tmp_path: Path) -> None:
    """A green banner must not be able to hide a stopped service."""
    _healthy(tmp_path)
    write_heartbeat(
        tmp_path, "settlement", interval_seconds=300, now=NOW - timedelta(hours=2)
    )
    assert _status(tmp_path)["overall"] == "degraded"


def test_a_late_service_warns(tmp_path: Path) -> None:
    _healthy(tmp_path)
    write_heartbeat(tmp_path, "engine", interval_seconds=30, now=NOW - timedelta(seconds=75))
    assert _status(tmp_path)["overall"] == "warn"


def test_no_recordings_warns_even_with_live_heartbeats(tmp_path: Path) -> None:
    """A running feed recording nothing is exactly the failure worth surfacing."""
    for name, interval in (("market", 60), ("engine", 30), ("settlement", 300)):
        write_heartbeat(tmp_path, name, interval_seconds=interval, now=NOW)
    assert _status(tmp_path)["overall"] == "warn"


# --------------------------------------------------------------------------- #
# Feed section — read from the tape, not a self-report                        #
# --------------------------------------------------------------------------- #
def test_feed_counts_ticks_and_reports_the_last_one(tmp_path: Path) -> None:
    market = tmp_path / "market"
    market.mkdir(parents=True)
    lines = [
        json.dumps({"snapshot": {"timestamp": (NOW - timedelta(minutes=2)).isoformat()}}),
        json.dumps(
            {
                "snapshot": {
                    "timestamp": (NOW - timedelta(minutes=1)).isoformat(),
                    "selected_providers": [{"component": "spot", "provider": "tradier"}],
                }
            }
        ),
    ]
    (market / "2026-07-22.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    feed = _status(tmp_path)["feed"]
    assert feed["state"] == "recording"
    assert feed["ticks"] == 2
    assert feed["provider"] == "tradier"
    assert 55 < feed["last_tick_age_seconds"] < 65


def test_feed_reports_no_recordings_before_the_first_tick(tmp_path: Path) -> None:
    assert _status(tmp_path)["feed"]["state"] == "no_recordings"


def test_feed_uses_the_latest_session(tmp_path: Path) -> None:
    market = tmp_path / "market"
    market.mkdir(parents=True)
    for session in ("2026-07-20", "2026-07-22", "2026-07-21"):
        (market / f"{session}.jsonl").write_text(
            json.dumps({"snapshot": {"timestamp": NOW.isoformat()}}) + "\n", encoding="utf-8"
        )
    feed = _status(tmp_path)["feed"]
    assert feed["session"] == "2026-07-22"
    assert feed["sessions_recorded"] == 3


def test_empty_recording_file_is_not_recording(tmp_path: Path) -> None:
    market = tmp_path / "market"
    market.mkdir(parents=True)
    (market / "2026-07-22.jsonl").write_text("", encoding="utf-8")
    assert _status(tmp_path)["feed"]["state"] == "empty"


# --------------------------------------------------------------------------- #
# AI section                                                                  #
# --------------------------------------------------------------------------- #
def test_ai_reports_the_live_gate_state(tmp_path: Path) -> None:
    status = _status(tmp_path)["ai"]
    assert status["allowed_now"] is True
    assert status["reason"] == "market_open"
    assert status["market_hours_only"] is True


def test_ai_reports_the_gate_closed_out_of_hours(tmp_path: Path) -> None:
    saturday = datetime(2026, 7, 25, 16, 0, tzinfo=UTC)
    status = build_system_status(tmp_path, now=saturday)["ai"]
    assert status["allowed_now"] is False
    assert status["reason"] == "market_closed"


def test_ai_reports_who_authored_the_last_decision(tmp_path: Path) -> None:
    """The check that confirms the gate is doing what it claims."""
    (tmp_path / "live_state.json").write_text(
        json.dumps(
            {
                "action": "ABSTAIN",
                "trader_model": "deterministic-v1",
                "provider": "deterministic",
                "generated_at": (NOW - timedelta(minutes=5)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    last = _status(tmp_path)["ai"]["last_decision"]
    assert last["trader_model"] == "deterministic-v1"
    assert 290 < last["age_seconds"] < 310


def test_missing_live_state_is_a_note_not_a_crash(tmp_path: Path) -> None:
    status = _status(tmp_path)["ai"]
    assert status["last_decision"] is None
    assert "not found" in status["note"]


# --------------------------------------------------------------------------- #
# Deploy section                                                              #
# --------------------------------------------------------------------------- #
def test_deploy_reports_the_live_commit(tmp_path: Path) -> None:
    """'Did my change land?' without `git rev-parse` over SSH."""
    (tmp_path / "deploy.json").write_text(
        json.dumps(
            {
                "commit_short": "b0ba921",
                "subject": "Port the Tradier provider",
                "deployed_at": (NOW - timedelta(minutes=8)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    deploy = _status(tmp_path)["deploy"]
    assert deploy["state"] == "ok"
    assert deploy["commit_short"] == "b0ba921"
    assert 470 < deploy["deployed_age_seconds"] < 490


def test_missing_deploy_file_is_unknown(tmp_path: Path) -> None:
    assert _status(tmp_path)["deploy"]["state"] == "unknown"


# --------------------------------------------------------------------------- #
# Reports section                                                             #
# --------------------------------------------------------------------------- #
def test_reports_summarize_dojo_and_validation(tmp_path: Path) -> None:
    for kind, body in (
        ("dojo", {"report_date": "2026-07-22", "summary": "ran", "flags": [{"a": 1}]}),
        ("validation", {"report_date": "2026-07-22", "summary": "gates", "ok": True}),
    ):
        directory = tmp_path / "reports" / kind
        directory.mkdir(parents=True)
        (directory / "latest.json").write_text(json.dumps(body), encoding="utf-8")
    reports = _status(tmp_path)["reports"]
    assert reports["dojo"]["flags"] == 1
    assert reports["validation"]["ok"] is True


def test_absent_reports_are_unavailable_not_fatal(tmp_path: Path) -> None:
    reports = _status(tmp_path)["reports"]
    assert reports["dojo"]["state"] == "unavailable"
    assert reports["validation"]["state"] == "unavailable"


# --------------------------------------------------------------------------- #
# Served over HTTP                                                            #
# --------------------------------------------------------------------------- #
def test_system_route_is_served(tmp_path: Path) -> None:
    from spy_der.runtime.dashboard_api import DashboardApiState, handle_get

    code, body = handle_get(DashboardApiState(tmp_path), "/v1/system")
    assert code == 200
    assert set(body) >= {"overall", "services", "feed", "ai", "deploy", "reports"}


def test_system_route_works_on_a_bare_state_root(tmp_path: Path) -> None:
    """It must answer before anything has ever run — that is when you need it."""
    from spy_der.runtime.dashboard_api import DashboardApiState, handle_get

    code, body = handle_get(DashboardApiState(tmp_path), "/v1/system")
    assert code == 200
    assert body["overall"] == "degraded"
