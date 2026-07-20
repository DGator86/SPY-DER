from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from spy_der.contracts.common import (
    ContractError,
    ErrorCode,
    ValidationError,
    content_hash,
    deterministic_id,
    require_finite,
    require_probability,
    require_tz_aware,
)
from spy_der.contracts.market import (
    FeedComponent,
    FeedStatus,
    OptionContract,
    OptionQuote,
    OptionType,
    SessionStatus,
)
from spy_der.market_data import (
    CanonicalSnapshotAssembler,
    MarketCalendar,
    SystemASnapshotAdapter,
    build_observation,
    classify_status,
)

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------- common ----
def test_require_tz_aware_rejects_naive() -> None:
    with pytest.raises(ValidationError) as exc:
        require_tz_aware(datetime(2026, 1, 5, 10, 0))
    assert exc.value.code is ErrorCode.NAIVE_TIMESTAMP


def test_require_finite_and_probability() -> None:
    assert require_finite(1.5) == 1.5
    with pytest.raises(ValidationError):
        require_finite(float("nan"))
    with pytest.raises(ValidationError):
        require_probability(1.5)


def test_content_hash_and_id_are_deterministic_and_order_independent() -> None:
    a = {"x": 1, "y": [1, 2, 3]}
    b = {"y": [1, 2, 3], "x": 1}
    assert content_hash(a) == content_hash(b)
    assert content_hash(a).startswith("sha256:")
    assert deterministic_id("snap", a) == deterministic_id("snap", b)
    assert deterministic_id("snap", a) != deterministic_id("snap", {"x": 2})


# -------------------------------------------------------------- calendar ----
def test_calendar_open_session_fields() -> None:
    cal = MarketCalendar()
    # 2026-01-05 is a Monday regular session; 14:30 ET is mid-session.
    ts = datetime(2026, 1, 5, 14, 30, tzinfo=ET)
    assert cal.is_session(ts)
    assert cal.is_open(ts)
    assert cal.session_date(ts) == date(2026, 1, 5)
    assert cal.session_status(ts) is SessionStatus.OPEN
    assert cal.minutes_from_open(ts) == 300  # 09:30 -> 14:30
    assert cal.minutes_to_close(ts) == 90  # 14:30 -> 16:00
    assert not cal.is_half_day(ts)


def test_calendar_session_date_uses_exchange_tz_not_utc() -> None:
    cal = MarketCalendar()
    # 2026-01-06 01:00 UTC is 2026-01-05 20:00 ET -> session date is the 5th.
    ts = datetime(2026, 1, 6, 1, 0, tzinfo=UTC)
    assert cal.session_date(ts) == date(2026, 1, 5)


def test_calendar_holiday_and_lockout() -> None:
    cal = MarketCalendar()
    # 2026-01-01 New Year's Day is a holiday.
    holiday = datetime(2026, 1, 1, 12, 0, tzinfo=ET)
    assert not cal.is_session(holiday)
    assert cal.session_status(holiday) is SessionStatus.HOLIDAY
    assert cal.in_entry_lockout(holiday)  # no session -> locked out

    near_close = datetime(2026, 1, 5, 15, 50, tzinfo=ET)
    assert cal.in_entry_lockout(near_close)  # within 15m of 16:00
    assert not cal.in_entry_lockout(datetime(2026, 1, 5, 14, 0, tzinfo=ET))


def test_calendar_settlement_available_after_close() -> None:
    cal = MarketCalendar()
    assert cal.settlement_available(datetime(2026, 1, 5, 16, 30, tzinfo=ET))
    assert not cal.settlement_available(datetime(2026, 1, 5, 12, 0, tzinfo=ET))


# ------------------------------------------------------------- freshness ----
def test_classify_status_precedence() -> None:
    assert classify_status(None, 60, present=False) is FeedStatus.MISSING
    assert classify_status(10, 60, valid=False) is FeedStatus.INVALID
    assert classify_status(-1, 60) is FeedStatus.INVALID
    assert classify_status(10, 60) is FeedStatus.LIVE
    assert classify_status(90, 60) is FeedStatus.DELAYED
    assert classify_status(500, 60) is FeedStatus.STALE
    assert classify_status(10, 60, fallback_used=True) is FeedStatus.FALLBACK


def test_build_observation_computes_age() -> None:
    received = datetime(2026, 1, 5, 14, 30, tzinfo=ET)
    observed = received - timedelta(seconds=5)
    obs = build_observation(
        FeedComponent.SPOT, "tradier", received, 60.0, observed_at=observed
    )
    assert obs.age_seconds == 5.0
    assert obs.status is FeedStatus.LIVE


# ------------------------------------------------------------- assembler ----
def _quote(strike: str, kind: OptionType, received: datetime) -> OptionQuote:
    contract = OptionContract(
        contract_id=f"SPY-{strike}-{kind.value}",
        underlying_symbol="SPY",
        expiration=date(2026, 1, 5),
        option_type=kind,
        strike=Decimal(strike),
    )
    return OptionQuote(contract=contract, received_at=received, source="tradier")


def test_assembler_is_deterministic_and_health_aware() -> None:
    cal = MarketCalendar()
    assembler = CanonicalSnapshotAssembler(cal)
    ts = datetime(2026, 1, 5, 14, 30, tzinfo=ET)
    chain = (_quote("500", OptionType.CALL, ts), _quote("500", OptionType.PUT, ts))
    obs = tuple(
        build_observation(c, "tradier", ts, 60.0, observed_at=ts)
        for c in (
            FeedComponent.SPOT,
            FeedComponent.BARS,
            FeedComponent.OPTION_CHAIN,
            FeedComponent.SETTLEMENT,
        )
    )
    snap1 = assembler.assemble(
        timestamp=ts,
        underlying_symbol="SPY",
        underlying_price=Decimal("500.12"),
        option_chain=chain,
        feed_observations=obs,
    )
    snap2 = assembler.assemble(
        timestamp=ts,
        underlying_symbol="SPY",
        underlying_price=Decimal("500.12"),
        option_chain=chain,
        feed_observations=obs,
    )
    assert snap1.snapshot_id == snap2.snapshot_id
    assert snap1.content_hash == snap2.content_hash
    assert snap1.is_live
    assert snap1.missing_components == ()
    assert snap1.chain_coverage.contracts_total == 2
    assert snap1.chain_coverage.has_calls and snap1.chain_coverage.has_puts


def test_assembler_flags_missing_required_component() -> None:
    assembler = CanonicalSnapshotAssembler()
    ts = datetime(2026, 1, 5, 14, 30, tzinfo=ET)
    # Only spot present; bars/chain/settlement missing.
    obs = (build_observation(FeedComponent.SPOT, "tradier", ts, 60.0, observed_at=ts),)
    snap = assembler.assemble(
        timestamp=ts,
        underlying_symbol="SPY",
        underlying_price=Decimal("500"),
        feed_observations=obs,
    )
    assert not snap.is_live
    assert "bars" in snap.missing_components
    assert not snap.data_quality.is_healthy


def test_assembler_rejects_naive_timestamp() -> None:
    assembler = CanonicalSnapshotAssembler()
    with pytest.raises(ValidationError):
        assembler.assemble(
            timestamp=datetime(2026, 1, 5, 14, 30),
            underlying_symbol="SPY",
            underlying_price=Decimal("500"),
        )


# --------------------------------------------------------- legacy adapter ----
def test_adapter_maps_system_a_dict() -> None:
    adapter = SystemASnapshotAdapter()
    legacy = {
        "symbol": "SPY",
        "ts": "2026-01-05T14:30:00-05:00",
        "session_date": "2026-01-05",
        "market": {"spot": 500.12, "bid": 500.10, "ask": 500.14},
        "feed_statuses": {"spot": "LIVE", "bars": "LIVE", "chain": "LIVE", "settlement": "LIVE"},
        "source_ages_seconds": {"spot": 1.0, "bars": 2.0},
    }
    snap = adapter.adapt(legacy)
    assert snap.underlying_symbol == "SPY"
    assert snap.underlying_price == Decimal("500.12")
    assert snap.session_date == date(2026, 1, 5)
    assert snap.is_live


def test_adapter_fails_closed_on_missing_spot() -> None:
    adapter = SystemASnapshotAdapter()
    with pytest.raises(ContractError) as exc:
        adapter.adapt({"symbol": "SPY", "ts": "2026-01-05T14:30:00-05:00"})
    assert exc.value.code is ErrorCode.MISSING_REQUIRED_INPUT


def test_adapter_rejects_naive_ts() -> None:
    adapter = SystemASnapshotAdapter()
    with pytest.raises(ValidationError):
        adapter.adapt({"symbol": "SPY", "ts": "2026-01-05T14:30:00", "spot": 500})
