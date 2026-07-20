from __future__ import annotations

import copy
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from spy_der.contracts.common import ErrorCode, content_hash
from spy_der.contracts.market import Bar, OptionContract, OptionQuote, OptionType
from spy_der.market_data import (
    CompositeFeed,
    CorruptRecordingError,
    RawTick,
    ReplayFeed,
    SnapshotRecorder,
    StaticProvider,
)

ET = ZoneInfo("America/New_York")
TS = datetime(2026, 1, 5, 14, 30, tzinfo=ET)


def _tick(provider: str, price: str, *, has_chain: bool = True) -> RawTick:
    contract = OptionContract(
        contract_id="SPY-500-CALL",
        underlying_symbol="SPY",
        expiration=date(2026, 1, 5),
        option_type=OptionType.CALL,
        strike=Decimal("500"),
    )
    quote = OptionQuote(contract=contract, received_at=TS, source=provider)
    bar = Bar(TS, Decimal("500"), Decimal("501"), Decimal("499"), Decimal("500"), 1000)
    return RawTick(
        provider=provider,
        symbol="SPY",
        observed_at=TS,
        underlying_price=Decimal(price),
        bars_1m=(bar,) if has_chain else (),
        option_chain=(quote,) if has_chain else (),
        has_chain=has_chain,
    )


# ------------------------------------------------------------- composite ----
def test_composite_primary_wins() -> None:
    primary = StaticProvider("tradier", [_tick("tradier", "500.10")])
    secondary = StaticProvider("massive", [_tick("massive", "499.90")])
    feed = CompositeFeed([primary, secondary])
    snap = feed.snapshot(TS)
    assert snap is not None
    assert feed.last_source == "tradier"
    assert snap.underlying_price == Decimal("500.10")
    # No settlement provider configured, so settlement is a required-but-missing
    # component and the snapshot is not fully live (spec §13.2, fail-closed).
    assert not snap.is_live
    assert snap.missing_components == ("settlement",)
    spot_status = {o.component.value: o.status for o in snap.feed_observations}["spot"]
    assert spot_status.value == "LIVE"


def test_composite_fails_over_to_secondary() -> None:
    empty = StaticProvider("tradier", [])  # returns None at TS
    secondary = StaticProvider("massive", [_tick("massive", "499.90")])
    feed = CompositeFeed([empty, secondary])
    snap = feed.snapshot(TS)
    assert snap is not None
    assert feed.last_source == "massive"
    selections = {s.component.value: s for s in snap.selected_providers}
    assert selections["spot"].fallback_used is True
    assert selections["spot"].attempt_order == 1


def test_composite_returns_none_when_all_dark() -> None:
    feed = CompositeFeed([StaticProvider("a", []), StaticProvider("b", [])])
    assert feed.snapshot(TS) is None
    assert feed.last_source is None


def test_composite_missing_chain_marks_component() -> None:
    feed = CompositeFeed([StaticProvider("yahoo", [_tick("yahoo", "500", has_chain=False)])])
    snap = feed.snapshot(TS)
    assert snap is not None
    assert "option_chain" in snap.missing_components
    assert not snap.is_live


def test_composite_settlement_backstop() -> None:
    primary = StaticProvider("tradier", [_tick("tradier", "500.10")])
    settle = StaticProvider("yahoo", [_tick("yahoo", "500.00")])
    feed = CompositeFeed([primary], settlement_provider=settle)
    snap = feed.snapshot(TS)
    assert snap is not None
    assert snap.is_live  # settlement now provided by backstop


# ------------------------------------------------------ recording/replay ----
def _recording() -> SnapshotRecorder:
    feed = CompositeFeed([StaticProvider("tradier", [_tick("tradier", "500.10")])])
    snap = feed.snapshot(TS)
    assert snap is not None
    rec = SnapshotRecorder()
    rec.record(snap)
    rec.record(snap)
    return rec


def test_record_replay_round_trip_is_deterministic() -> None:
    rec = _recording()
    text1 = rec.to_jsonl()
    text2 = _recording().to_jsonl()
    assert text1 == text2  # identical snapshots -> identical bytes
    replay = ReplayFeed.from_jsonl(text1, expected_schema="1.0.0")
    assert len(replay) == 2
    snapshots = list(replay.replay())
    assert len(snapshots) == 2
    assert snapshots[0]["underlying_price"] == "500.10"


def test_replay_detects_hash_tampering() -> None:
    records = list(_recording().records)
    records[1] = copy.deepcopy(records[1])
    records[1]["snapshot"]["underlying_price"] = "999.99"  # content changed, hash stale
    with pytest.raises(CorruptRecordingError) as exc:
        ReplayFeed(records)
    assert exc.value.code is ErrorCode.RECORD_HASH_MISMATCH


def test_replay_detects_sequence_gap() -> None:
    records = copy.deepcopy(list(_recording().records))
    records[1]["seq"] = 5  # break continuity 0 -> 5
    records[1]["record_hash"] = content_hash(records[1]["snapshot"])
    with pytest.raises(CorruptRecordingError) as exc:
        ReplayFeed(records)
    assert exc.value.code is ErrorCode.SEQUENCE_GAP


def test_replay_detects_schema_mismatch() -> None:
    text = _recording().to_jsonl()
    with pytest.raises(CorruptRecordingError) as exc:
        ReplayFeed.from_jsonl(text, expected_schema="9.9.9")
    assert exc.value.code is ErrorCode.SCHEMA_MISMATCH


def test_replay_detects_malformed_record() -> None:
    with pytest.raises(CorruptRecordingError) as exc:
        ReplayFeed([{"seq": 0}])
    assert exc.value.code is ErrorCode.MALFORMED_RECORD
