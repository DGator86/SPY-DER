"""Canonical observation construction (master spec §25 / System A prediction/dataset.py).

One observation = symbol + session_date + decision timestamp, identified by a
stable snapshot_id:

    SHA256(symbol | normalized ET timestamp | feature version | source seq)
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from spy_der.contracts.common import require_tz_aware
from spy_der.contracts.forecasts import FEATURE_VERSION, LABEL_VERSION
from spy_der.market_data.calendar import MarketCalendar

__all__ = [
    "FEATURE_VERSION",
    "LABEL_VERSION",
    "ObservationRow",
    "build_observation",
    "make_snapshot_id",
    "normalize_ts",
    "session_metadata",
]

ET = ZoneInfo("America/New_York")
_CALENDAR = MarketCalendar()


def normalize_ts(ts: dt.datetime) -> str:
    """Canonical ET timestamp string (second precision) for hashing."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ET)
    else:
        require_tz_aware(ts, "normalize_ts")
    return ts.astimezone(ET).replace(microsecond=0).isoformat()


def make_snapshot_id(
    symbol: str,
    ts: dt.datetime,
    feature_version: str = FEATURE_VERSION,
    source_seq: int = 0,
) -> str:
    payload = f"{symbol}|{normalize_ts(ts)}|{feature_version}|{source_seq}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def session_metadata(ts: dt.datetime) -> dict[str, Any]:
    """Exchange-session metadata; non-sessions return is_session=False."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ET)
    else:
        require_tz_aware(ts, "session_metadata")
    ts_et = ts.astimezone(ET)
    date_str = ts_et.date().isoformat()
    out: dict[str, Any] = {
        "session_date": date_str,
        "is_session": False,
        "session_open": None,
        "session_close": None,
        "is_early_close": None,
        "minutes_since_open": None,
        "minutes_to_close": None,
        "day_of_week": ts_et.weekday(),
    }
    if not _CALENDAR.is_session(ts):
        return out
    open_et = _CALENDAR.session_open(ts)
    close_et = _CALENDAR.session_close(ts)
    mso = _CALENDAR.minutes_from_open(ts)
    mtc = _CALENDAR.minutes_to_close(ts)
    out.update(
        {
            "is_session": True,
            "session_open": open_et.isoformat() if open_et else None,
            "session_close": close_et.isoformat() if close_et else None,
            "is_early_close": _CALENDAR.is_half_day(ts),
            "minutes_since_open": float(mso) if mso is not None else None,
            "minutes_to_close": float(mtc) if mtc is not None else None,
        }
    )
    return out


@dataclass(frozen=True, slots=True)
class ObservationRow:
    snapshot_id: str
    symbol: str
    session_date: str
    ts: str
    minutes_since_open: float | None
    minutes_to_close: float | None
    spot: float
    feature_version: str
    features: dict[str, float | None] = field(default_factory=dict)
    standardized: dict[str, float] = field(default_factory=dict)
    missingness: dict[str, int] = field(default_factory=dict)
    source_ages: dict[str, float | None] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def build_observation(
    symbol: str,
    ts: dt.datetime,
    spot: float,
    *,
    features: dict[str, float | None],
    standardized: dict[str, float] | None = None,
    missingness: dict[str, int] | None = None,
    source_ages: dict[str, float | None] | None = None,
    quality: dict[str, Any] | None = None,
    feature_version: str = FEATURE_VERSION,
    source_seq: int = 0,
) -> ObservationRow:
    """Build a leakage-safe observation row with deterministic snapshot_id."""
    meta = session_metadata(ts)
    q = dict(quality or {})
    q.setdefault("is_session", meta["is_session"])
    if meta["is_early_close"] is not None:
        q.setdefault("is_early_close", meta["is_early_close"])
    return ObservationRow(
        snapshot_id=make_snapshot_id(symbol, ts, feature_version, source_seq),
        symbol=symbol,
        session_date=str(meta["session_date"]),
        ts=normalize_ts(ts),
        minutes_since_open=meta["minutes_since_open"],
        minutes_to_close=meta["minutes_to_close"],
        spot=float(spot),
        feature_version=feature_version,
        features=dict(features),
        standardized=dict(standardized or {}),
        missingness=dict(missingness or {}),
        source_ages=dict(source_ages or {}),
        quality=q,
    )
