"""Exchange session calendar (master spec §14).

Migrated from System A ``market_calendar.py`` (0DTE @ de4a6e7): NYSE/XNYS
sessions, holidays, half-days, DST, open/close, exchange session date, minutes
from/to open, entry lockout, and settlement availability. Statistical grouping
uses the exchange session date, never the UTC date (spec §14).

Backed by ``exchange_calendars`` for holiday and early-close rules; timestamps
cross the boundary as timezone-aware ``datetime`` objects only.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from typing import cast
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

from spy_der.contracts.common import require_tz_aware
from spy_der.contracts.market import SessionStatus

_ET = ZoneInfo("America/New_York")
_REGULAR_CLOSE_HOUR_ET = 16
_DEFAULT_ENTRY_LOCKOUT_MINUTES = 15

__all__ = ["MarketCalendar"]


@lru_cache(maxsize=4)
def _calendar(exchange: str) -> xcals.ExchangeCalendar:
    return cast("xcals.ExchangeCalendar", xcals.get_calendar(exchange))


class MarketCalendar:
    """Timezone-aware wrapper over an ``exchange_calendars`` calendar."""

    def __init__(
        self,
        exchange: str = "XNYS",
        entry_lockout_minutes: int = _DEFAULT_ENTRY_LOCKOUT_MINUTES,
    ) -> None:
        self.exchange = exchange
        self.entry_lockout_minutes = entry_lockout_minutes

    @property
    def _cal(self) -> xcals.ExchangeCalendar:
        return _calendar(self.exchange)

    @staticmethod
    def _to_et(ts: dt.datetime) -> dt.datetime:
        require_tz_aware(ts, "MarketCalendar timestamp")
        return ts.astimezone(_ET)

    def session_date(self, ts: dt.datetime) -> dt.date:
        """Exchange session date (ET), the canonical grouping key (spec §14)."""
        return self._to_et(ts).date()

    def is_session(self, ts: dt.datetime) -> bool:
        return bool(self._cal.is_session(self.session_date(ts).isoformat()))

    def _open_close(self, session: dt.date) -> tuple[dt.datetime, dt.datetime] | None:
        iso = session.isoformat()
        if not self._cal.is_session(iso):
            return None
        open_ts = cast("dt.datetime", self._cal.session_open(iso).to_pydatetime()).astimezone(_ET)
        close_ts = cast("dt.datetime", self._cal.session_close(iso).to_pydatetime()).astimezone(_ET)
        return open_ts, close_ts

    def session_open(self, ts: dt.datetime) -> dt.datetime | None:
        bounds = self._open_close(self.session_date(ts))
        return bounds[0] if bounds else None

    def session_close(self, ts: dt.datetime) -> dt.datetime | None:
        bounds = self._open_close(self.session_date(ts))
        return bounds[1] if bounds else None

    def is_open(self, ts: dt.datetime) -> bool:
        """True when the regular session is open at ``ts``."""
        bounds = self._open_close(self.session_date(ts))
        if bounds is None:
            return False
        et = self._to_et(ts)
        return bounds[0] <= et < bounds[1]

    def is_half_day(self, ts: dt.datetime) -> bool:
        """True when the session closes before the regular 16:00 ET close."""
        bounds = self._open_close(self.session_date(ts))
        if bounds is None:
            return False
        return bounds[1].hour < _REGULAR_CLOSE_HOUR_ET

    def session_status(self, ts: dt.datetime) -> SessionStatus:
        bounds = self._open_close(self.session_date(ts))
        if bounds is None:
            return SessionStatus.HOLIDAY
        et = self._to_et(ts)
        if et < bounds[0]:
            return SessionStatus.PRE_OPEN
        if et >= bounds[1]:
            return SessionStatus.CLOSED
        return SessionStatus.OPEN

    def minutes_from_open(self, ts: dt.datetime) -> int | None:
        bounds = self._open_close(self.session_date(ts))
        if bounds is None:
            return None
        et = self._to_et(ts)
        if et < bounds[0]:
            return None
        return int((et - bounds[0]).total_seconds() // 60)

    def minutes_to_close(self, ts: dt.datetime) -> int | None:
        bounds = self._open_close(self.session_date(ts))
        if bounds is None:
            return None
        et = self._to_et(ts)
        if et > bounds[1]:
            return None
        return int((bounds[1] - et).total_seconds() // 60)

    def in_entry_lockout(self, ts: dt.datetime, lockout_minutes: int | None = None) -> bool:
        """True inside the no-new-entry window before close (spec §14, §23)."""
        remaining = self.minutes_to_close(ts)
        if remaining is None:
            return True
        limit = self.entry_lockout_minutes if lockout_minutes is None else lockout_minutes
        return remaining <= limit

    def settlement_available(self, ts: dt.datetime) -> bool:
        """True once the session has closed (settlement can be observed)."""
        bounds = self._open_close(self.session_date(ts))
        if bounds is None:
            return False
        return self._to_et(ts) >= bounds[1]
