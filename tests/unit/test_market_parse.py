"""`snapshot_from_dict` — the inverse of canonical snapshot serialization.

The round-trip is what makes `spy-der engine` possible: a recording is only
replayable if the dict `ReplayFeed` yields can become a typed snapshot again.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from spy_der.candidates.factory import generate_candidate_universe
from spy_der.contracts.market import (
    Bar,
    CanonicalMarketSnapshot,
    CatalystState,
    ChainCoverage,
    DataQuality,
    FeedComponent,
    FeedObservation,
    FeedStatus,
    OptionContract,
    OptionQuote,
    OptionType,
    ProviderSelection,
    SessionStatus,
)
from spy_der.contracts.market_parse import SnapshotParseError, snapshot_from_dict
from spy_der.contracts.serialization import to_canonical_json


def _quote(strike: str, option_type: OptionType) -> OptionQuote:
    exp = date(2026, 1, 5)
    right = "C" if option_type is OptionType.CALL else "P"
    return OptionQuote(
        contract=OptionContract(
            contract_id=f"SPY{exp:%y%m%d}{right}{int(Decimal(strike) * 1000):08d}",
            underlying_symbol="SPY",
            expiration=exp,
            option_type=option_type,
            strike=Decimal(strike),
        ),
        received_at=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        source="test",
        bid=Decimal("1.00"),
        ask=Decimal("1.10"),
        mark=Decimal("1.05"),
        volume=12,
        open_interest=340,
        implied_volatility=0.21,
        delta=0.42,
        observed_at=datetime(2026, 1, 5, 14, 59, 55, tzinfo=UTC),
        age_seconds=5.0,
        quality_flags=("wide_spread",),
    )


def _full_snapshot() -> CanonicalMarketSnapshot:
    """Every optional field populated — a partial fixture proves little."""
    return CanonicalMarketSnapshot(
        snapshot_id="snap-parse",
        content_hash="sha256:parse",
        timestamp=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        session_date=date(2026, 1, 5),
        underlying_symbol="SPY",
        underlying_price=Decimal("100.25"),
        session_status=SessionStatus.OPEN,
        normalization_version="1.2.3",
        underlying_bid=Decimal("100.24"),
        underlying_ask=Decimal("100.26"),
        minutes_from_open=30,
        minutes_to_close=360,
        bars_1m=(
            Bar(
                timestamp=datetime(2026, 1, 5, 14, 59, tzinfo=UTC),
                open=Decimal("100.0"),
                high=Decimal("100.5"),
                low=Decimal("99.9"),
                close=Decimal("100.25"),
                volume=1500,
            ),
        ),
        option_chain=(_quote("99", OptionType.PUT), _quote("101", OptionType.CALL)),
        feed_observations=(
            FeedObservation(
                component=FeedComponent.SPOT,
                provider="massive",
                received_at=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
                status=FeedStatus.LIVE,
                freshness_limit_seconds=60.0,
                observed_at=datetime(2026, 1, 5, 14, 59, 58, tzinfo=UTC),
                age_seconds=2.0,
                attempt_order=1,
                fallback_used=False,
                error_code=None,
            ),
        ),
        selected_providers=(
            ProviderSelection(
                component=FeedComponent.SPOT, provider="massive", attempt_order=1
            ),
        ),
        chain_coverage=ChainCoverage(
            contracts_total=2,
            strikes_total=2,
            min_strike=Decimal("99"),
            max_strike=Decimal("101"),
            has_calls=True,
            has_puts=True,
        ),
        catalyst_state=CatalystState(lockout_active=True, reason="fomc"),
        data_quality=DataQuality(is_healthy=False, penalty=0.25, flags=("thin_chain",)),
        missing_components=("breadth",),
    )


def _as_dict(snapshot: CanonicalMarketSnapshot) -> dict:
    return json.loads(to_canonical_json(snapshot))


# --------------------------------------------------------------------------- #
# Round-trip                                                                  #
# --------------------------------------------------------------------------- #
def test_round_trip_is_equal_and_byte_identical() -> None:
    original = _full_snapshot()
    blob = to_canonical_json(original)
    rebuilt = snapshot_from_dict(json.loads(blob))
    assert rebuilt == original
    assert to_canonical_json(rebuilt) == blob


def test_round_trip_preserves_decimal_precision() -> None:
    """A float round-trip would silently lose Decimal exactness on prices."""
    rebuilt = snapshot_from_dict(_as_dict(_full_snapshot()))
    assert rebuilt.underlying_price == Decimal("100.25")
    assert isinstance(rebuilt.underlying_price, Decimal)
    assert isinstance(rebuilt.option_chain[0].contract.strike, Decimal)


def test_rebuilt_snapshot_produces_an_identical_candidate_universe() -> None:
    """The property `spy-der engine` depends on: replay reproduces the pipeline."""
    original = _full_snapshot()
    rebuilt = snapshot_from_dict(_as_dict(original))
    assert to_canonical_json(generate_candidate_universe(rebuilt)) == to_canonical_json(
        generate_candidate_universe(original)
    )


def test_enums_come_back_as_enums_not_strings() -> None:
    rebuilt = snapshot_from_dict(_as_dict(_full_snapshot()))
    assert rebuilt.session_status is SessionStatus.OPEN
    assert rebuilt.feed_observations[0].status is FeedStatus.LIVE
    assert rebuilt.feed_observations[0].component is FeedComponent.SPOT
    assert rebuilt.option_chain[0].contract.option_type in tuple(OptionType)


def test_minimal_snapshot_round_trips() -> None:
    minimal = CanonicalMarketSnapshot(
        snapshot_id="s",
        content_hash="h",
        timestamp=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        session_date=date(2026, 1, 5),
        underlying_symbol="SPY",
        underlying_price=Decimal("100"),
        session_status=SessionStatus.CLOSED,
    )
    assert snapshot_from_dict(_as_dict(minimal)) == minimal


# --------------------------------------------------------------------------- #
# Fail-closed parsing                                                         #
# --------------------------------------------------------------------------- #
def test_missing_required_field_raises() -> None:
    data = _as_dict(_full_snapshot())
    del data["underlying_price"]
    with pytest.raises(SnapshotParseError, match="underlying_price"):
        snapshot_from_dict(data)


def test_naive_datetime_is_rejected_not_localized() -> None:
    """The contracts require tz-aware; a naive value in a recording is corruption."""
    data = _as_dict(_full_snapshot())
    data["timestamp"] = "2026-01-05T15:00:00"
    with pytest.raises(SnapshotParseError, match="timezone-aware"):
        snapshot_from_dict(data)


def test_unknown_enum_value_raises() -> None:
    data = _as_dict(_full_snapshot())
    data["session_status"] = "BRUNCH"
    with pytest.raises(SnapshotParseError, match="SessionStatus"):
        snapshot_from_dict(data)


def test_undecodable_decimal_raises() -> None:
    data = _as_dict(_full_snapshot())
    data["underlying_price"] = "not-a-number"
    with pytest.raises(SnapshotParseError, match="decimal"):
        snapshot_from_dict(data)


def test_nested_failure_names_its_path() -> None:
    """An operator needs to know which leg of which quote is bad."""
    data = _as_dict(_full_snapshot())
    data["option_chain"][1]["contract"]["strike"] = "oops"
    with pytest.raises(SnapshotParseError, match=r"option_chain\[1\]\.contract\.strike"):
        snapshot_from_dict(data)


def test_wrong_container_type_raises() -> None:
    data = _as_dict(_full_snapshot())
    data["option_chain"] = {"not": "a list"}
    with pytest.raises(SnapshotParseError, match="option_chain"):
        snapshot_from_dict(data)
