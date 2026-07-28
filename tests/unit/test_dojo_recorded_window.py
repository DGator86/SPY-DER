"""Recorded-phase session windowing for the daily/recent Dojo timers."""

from __future__ import annotations

from datetime import date

from spy_der.dojo.config import DojoConfig
from spy_der.dojo.recorded import _filter_sessions, run_recorded_phase


def test_recent_days_keeps_last_n_sessions_not_calendar_window() -> None:
    """A Mon/Tue run must still see Friday when --recent-days 3.

    Calendar windowing (max - 2 days) drops Friday across the weekend and the
    daily Dojo then reports ``2 sessions (< 3)`` with a full tape on disk.
    """
    sessions = [
        date(2026, 7, 22),  # Wed
        date(2026, 7, 23),  # Thu
        date(2026, 7, 24),  # Fri
        date(2026, 7, 27),  # Mon
        date(2026, 7, 28),  # Tue
    ]
    assert _filter_sessions(sessions, 3) == [
        date(2026, 7, 24),
        date(2026, 7, 27),
        date(2026, 7, 28),
    ]


def test_recent_days_zero_keeps_everything() -> None:
    sessions = [date(2026, 7, 27), date(2026, 7, 28)]
    assert _filter_sessions(sessions, 0) == sessions


class _FakeTape:
    def __init__(self, sessions: list[date]) -> None:
        self._sessions = sessions

    def sessions(self) -> list[date]:
        return list(self._sessions)

    def snapshots(self, session: date):
        return []

    def outcome(self, snapshot_id: str):
        return None


def test_recorded_phase_reaches_min_sessions_across_a_weekend() -> None:
    tape = _FakeTape(
        [
            date(2026, 7, 24),
            date(2026, 7, 27),
            date(2026, 7, 28),
        ]
    )
    # Would fail under calendar windowing: only Mon+Tue fall in the last 3 days.
    result = run_recorded_phase(
        DojoConfig(recent_days=3, min_sessions=3, min_ticks=0),
        tape,
    )
    assert result["status"] != "insufficient_data"
    assert result["n_sessions"] == 3
