"""Import of 0DTE tick recordings into SPY-DER canonical recordings.

0DTE has been recording live ticks to `/var/lib/zerodte/ticks` for far longer
than SPY-DER has had a market service, so this is the difference between
training on real markets today and waiting weeks for fresh recordings.

The fixtures reproduce `chain_store.ChainRecorder`'s format *including its
quirks* — incremental bars, NaN for unavailable fields, naive-UTC bar stamps,
and a truncated final line from a crashed recorder. A test against a tidy
payload would pass while the real import stayed broken.
"""

from __future__ import annotations

import gzip
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from spy_der.contracts.market_parse import snapshot_from_dict
from spy_der.integrations.zerodte.tick_import import (
    FLAG_IMPORTED,
    import_directory,
    iter_session_snapshots,
)
from spy_der.market_data.replay import ReplayFeed

ET = ZoneInfo("America/New_York")
SESSION = "2026-01-05"
OPEN_UTC = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def _option_rows(spot: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for strike in range(int(spot) - 6, int(spot) + 7, 2):
        for side in ("call", "put"):
            intrinsic = max(spot - strike, 0.0) if side == "call" else max(strike - spot, 0.0)
            mid = intrinsic + 1.0
            rows.append(
                {
                    "side": side,
                    "strike": float(strike),
                    "oi": 1000 + strike,
                    "gamma": 0.02,
                    "bid": mid - 0.05,
                    "ask": mid + 0.05,
                    "delta": 0.5 if side == "call" else -0.5,
                    "volume": 120 if side == "call" else 150,
                    "dte_days": 0.0,
                }
            )
    return rows


def _market(spot: float, **overrides: Any) -> dict[str, Any]:
    """A `MarketSnapshot` as `_market_to_dict` writes it, NaNs included."""
    body: dict[str, Any] = {
        "spot": spot,
        "net_gex": -1.5e9,
        "gamma_flip": spot - 1.0,
        "call_wall": spot + 5.0,
        "put_wall": spot - 5.0,
        "gex_pct_rank": 0.62,
        "vix9d": 13.0,
        "vix": 15.0,
        "vix3m": 17.0,
        "vvix": 90.0,
        "vvix_baseline": 95.0,
        "straddle_breakeven": 3.1,
        "expected_range": 4.0,
        "adx": 22.0,
        "rsi": 48.0,
        "bb_width": 0.4,
        "bb_width_baseline": 0.5,
        "vwap": spot,
        "vwap_reversion_count": 2,
        "tick_abs_mean": 400.0,
        "cvd_slope": 0.1,
        "now": OPEN_UTC.isoformat(),
        "has_catalyst": False,
        "catalyst_label": "",
        "gex_rank_warm": True,
        # 0DTE writes NaN — not absence — when a feed is unavailable.
        "pcr_volume": 1.25,
        "volume_oi_ratio": 0.12,
        "rsp_spy_div": float("nan"),
        "sector_align": 0.55,
        "top10_pressure": float("nan"),
    }
    body.update(overrides)
    return body


def _bar_row(minute: int, close: float) -> list[Any]:
    """A recorded bar row: naive-UTC numpy stamp plus OHLCV."""
    stamp = (OPEN_UTC + timedelta(minutes=minute)).replace(tzinfo=None)
    return [
        f"{stamp.isoformat()}.000000000",
        close - 0.1,
        close + 0.2,
        close - 0.2,
        close,
        1000.0 + minute,
    ]


def _write(
    directory: Path,
    *,
    ticks: int = 6,
    session: str = SESSION,
    option_rows: bool = True,
    truncated_tail: bool = False,
    settle: float | None = 101.5,
) -> Path:
    """Write a recording in `ChainRecorder`'s exact shape."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"ticks_{session}.jsonl.gz"
    lines: list[str] = []
    for i in range(ticks):
        spot = 100.0 + i * 0.25
        record: dict[str, Any] = {
            "t": "tick",
            "ts": (OPEN_UTC + timedelta(minutes=i * 5)).isoformat(),
            "seq": i,
            "market": _market(spot, now=(OPEN_UTC + timedelta(minutes=i * 5)).isoformat()),
            "chain": {"spot": spot, "t_years": 0.002, "r": 0.05, "quotes": []},
            # Incremental: only the bars newer than the previous record.
            "bars": [_bar_row(i * 5 + k, spot) for k in range(5)],
            "option_rows": _option_rows(spot) if option_rows else None,
            "weekly_option_rows": None,
            "gex_feed_source": "tradier",
        }
        lines.append(json.dumps(record))
    if settle is not None:
        lines.append(json.dumps({"t": "settle", "date": session, "price": settle}))

    payload = "\n".join(lines) + "\n"
    if truncated_tail:
        payload += '{"t":"tick","ts":"2026-01-05T15:0'  # crashed mid-write
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(payload)
    return path


# --------------------------------------------------------------------------- #
# Reading the source format                                                   #
# --------------------------------------------------------------------------- #
def test_ticks_become_canonical_snapshots(tmp_path: Path) -> None:
    path = _write(tmp_path / "ticks")
    snapshots = [s for s, _ in iter_session_snapshots(path)]
    assert len(snapshots) == 6
    first = snapshots[0]
    assert first.underlying_symbol == "SPY"
    assert first.underlying_price > 0
    assert first.option_chain
    assert first.bars_1m


def test_bars_accumulate_because_the_source_stores_them_incrementally(
    tmp_path: Path,
) -> None:
    """Reading a tick's `bars` as the whole window would starve every feature.

    Each record holds only the bars newer than the previous one, so the window
    has to be reassembled across ticks — five per tick here, so the sixth
    snapshot must carry thirty.
    """
    path = _write(tmp_path / "ticks", ticks=6)
    snapshots = [s for s, _ in iter_session_snapshots(path)]
    assert len(snapshots[0].bars_1m) == 5
    assert len(snapshots[-1].bars_1m) == 30
    stamps = [b.timestamp for b in snapshots[-1].bars_1m]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)


def test_the_bar_window_is_bounded(tmp_path: Path) -> None:
    path = _write(tmp_path / "ticks", ticks=6)
    snapshots = [s for s, _ in iter_session_snapshots(path, bar_window=8)]
    assert len(snapshots[-1].bars_1m) == 8


def test_bar_timestamps_are_read_as_utc_not_localized(tmp_path: Path) -> None:
    """The stamps are naive numpy renderings of an epoch array.

    Attaching ET would shift every bar by five hours and misfile the session.
    """
    path = _write(tmp_path / "ticks", ticks=1)
    snapshot = next(iter(iter_session_snapshots(path)))[0]
    first = snapshot.bars_1m[0]
    assert first.timestamp.tzinfo is not None
    assert first.timestamp == OPEN_UTC


def test_option_rows_rebuild_a_full_canonical_chain(tmp_path: Path) -> None:
    """`option_rows` carries gamma/OI/delta — enough to recompute GEX."""
    path = _write(tmp_path / "ticks", ticks=1)
    snapshot = next(iter(iter_session_snapshots(path)))[0]
    quote = snapshot.option_chain[0]
    assert quote.gamma == 0.02
    assert quote.open_interest is not None and quote.open_interest > 0
    assert quote.delta is not None
    assert quote.bid is not None and quote.ask is not None
    assert quote.mark == (quote.bid + quote.ask) / 2
    assert snapshot.chain_coverage.has_calls and snapshot.chain_coverage.has_puts


def test_features_can_be_recomputed_from_an_imported_snapshot(tmp_path: Path) -> None:
    """The point of importing at all: it has to feed the feature pipeline."""
    from spy_der.features.pipeline import SnapshotFeaturePipeline

    path = _write(tmp_path / "ticks", ticks=6)
    snapshot = [s for s, _ in iter_session_snapshots(path)][-1]
    features = dict(SnapshotFeaturePipeline().build(snapshot).features)
    assert any(k.startswith("gex.") for k in features)
    assert any(k.startswith("flow.") for k in features)
    assert any(k.startswith("vix.") for k in features)
    assert any(k.startswith("1m.") for k in features)


# --------------------------------------------------------------------------- #
# NaN is absence, never zero                                                  #
# --------------------------------------------------------------------------- #
def test_nan_fields_become_absent_not_zero(tmp_path: Path) -> None:
    """A zero `rsp_spy_div` asserts perfectly neutral breadth.

    0DTE writes NaN for an unavailable feed. Coercing that to 0 would
    manufacture a confident reading out of a missing one.
    """
    path = _write(tmp_path / "ticks", ticks=1)
    snapshot = next(iter(iter_session_snapshots(path)))[0]
    breadth = snapshot.breadth
    assert breadth is not None
    assert breadth.rsp_spy_div is None       # NaN in the source
    assert breadth.top10_pressure is None    # NaN in the source
    assert breadth.sector_align == 0.55      # genuinely observed


def test_breadth_is_absent_when_every_component_is_nan(tmp_path: Path) -> None:
    path = _write(tmp_path / "ticks", ticks=1)
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    lines[0]["market"].update(
        rsp_spy_div=float("nan"), sector_align=float("nan"), top10_pressure=float("nan")
    )
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(json.dumps(row) for row in lines) + "\n")

    snapshot = next(iter(iter_session_snapshots(path)))[0]
    assert snapshot.breadth is None


def test_the_volatility_surface_is_carried_over(tmp_path: Path) -> None:
    path = _write(tmp_path / "ticks", ticks=1)
    snapshot = next(iter(iter_session_snapshots(path)))[0]
    term = snapshot.volatility_term_structure
    assert term is not None
    assert (term.vix, term.vix9d, term.vix3m, term.vvix) == (15.0, 13.0, 17.0, 90.0)
    assert term.vvix_baseline == 95.0


def test_a_nan_vix_yields_no_term_structure(tmp_path: Path) -> None:
    path = _write(tmp_path / "ticks", ticks=1)
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    lines[0]["market"]["vix"] = float("nan")
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(json.dumps(row) for row in lines) + "\n")

    snapshot = next(iter(iter_session_snapshots(path)))[0]
    assert snapshot.volatility_term_structure is None


def test_no_imported_value_is_a_nan(tmp_path: Path) -> None:
    """The contracts reject non-finite values; the import must never emit one."""
    path = _write(tmp_path / "ticks", ticks=3)
    for snapshot, _ in iter_session_snapshots(path):
        for quote in snapshot.option_chain:
            for name in ("gamma", "delta", "implied_volatility"):
                value = getattr(quote, name)
                assert value is None or math.isfinite(value)


# --------------------------------------------------------------------------- #
# Robustness against real recorder behaviour                                  #
# --------------------------------------------------------------------------- #
def test_a_truncated_tail_from_a_crash_does_not_lose_the_session(
    tmp_path: Path,
) -> None:
    """A crashed recorder leaves a partial final line; the rest is still good."""
    path = _write(tmp_path / "ticks", ticks=4, truncated_tail=True)
    snapshots = [s for s, _ in iter_session_snapshots(path)]
    assert len(snapshots) == 4


def test_older_recordings_without_option_rows_still_import(tmp_path: Path) -> None:
    """`option_rows` post-dates the recorder; those ticks keep spot and bars."""
    path = _write(tmp_path / "ticks", ticks=3, option_rows=False)
    results = list(iter_session_snapshots(path))
    assert len(results) == 3
    snapshot, stats = results[-1]
    assert stats["has_chain"] is False
    assert snapshot.option_chain == ()
    assert snapshot.bars_1m
    assert "option_chain" in snapshot.missing_components


def test_a_tick_without_a_usable_spot_is_dropped(tmp_path: Path) -> None:
    """Fabricating a spot would reshape every moneyness band downstream."""
    path = _write(tmp_path / "ticks", ticks=2)
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    lines[0]["market"]["spot"] = float("nan")
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(json.dumps(row) for row in lines) + "\n")

    assert len([s for s, _ in iter_session_snapshots(path)]) == 1


# --------------------------------------------------------------------------- #
# Writing SPY-DER recordings                                                  #
# --------------------------------------------------------------------------- #
def test_import_writes_a_verifiable_spy_der_recording(tmp_path: Path) -> None:
    """Imported sessions must pass the same integrity reader as live ones."""
    _write(tmp_path / "ticks")
    result = import_directory(tmp_path / "ticks", tmp_path / "state")
    assert result.sessions_written == (SESSION,)
    assert result.snapshots_written == 6

    recording = tmp_path / "state" / "market" / f"{SESSION}.jsonl"
    feed = ReplayFeed.from_file(recording)  # verifies hashes and sequence
    payloads = list(feed.replay())
    assert len(payloads) == 6
    rebuilt = snapshot_from_dict(payloads[0])
    assert rebuilt.underlying_symbol == "SPY"


def test_imported_snapshots_are_marked_as_imported(tmp_path: Path) -> None:
    """Provenance survives: an imported tick is auditable as such."""
    _write(tmp_path / "ticks")
    import_directory(tmp_path / "ticks", tmp_path / "state")
    feed = ReplayFeed.from_file(tmp_path / "state" / "market" / f"{SESSION}.jsonl")
    snapshot = snapshot_from_dict(next(iter(feed.replay())))
    assert FLAG_IMPORTED in snapshot.data_quality.flags


def test_settlements_are_collected(tmp_path: Path) -> None:
    _write(tmp_path / "ticks", settle=101.5)
    result = import_directory(tmp_path / "ticks", tmp_path / "state")
    assert result.settlements == {SESSION: 101.5}


def test_reimport_is_skipped_unless_overwrite(tmp_path: Path) -> None:
    _write(tmp_path / "ticks")
    first = import_directory(tmp_path / "ticks", tmp_path / "state")
    assert first.snapshots_written == 6

    second = import_directory(tmp_path / "ticks", tmp_path / "state")
    assert second.snapshots_written == 0
    assert second.skipped and "already imported" in second.skipped[0][1]

    third = import_directory(tmp_path / "ticks", tmp_path / "state", overwrite=True)
    assert third.snapshots_written == 6


def test_sessions_can_be_selected(tmp_path: Path) -> None:
    _write(tmp_path / "ticks", session="2026-01-05")
    _write(tmp_path / "ticks", session="2026-01-06")
    result = import_directory(
        tmp_path / "ticks", tmp_path / "state", sessions=["2026-01-06"]
    )
    assert result.sessions_written == ("2026-01-06",)


def test_the_source_directory_is_never_written_to(tmp_path: Path) -> None:
    """0DTE is still live; the import must be strictly read-only on its data."""
    source = tmp_path / "ticks"
    _write(source)
    before = {p.name: p.stat().st_mtime_ns for p in source.iterdir()}
    import_directory(source, tmp_path / "state")
    after = {p.name: p.stat().st_mtime_ns for p in source.iterdir()}
    assert before == after


def test_a_corrupt_archive_costs_its_session_not_the_run(tmp_path: Path) -> None:
    source = tmp_path / "ticks"
    _write(source, session="2026-01-05")
    (source / "ticks_2026-01-06.jsonl.gz").write_bytes(b"not a gzip archive at all")

    result = import_directory(source, tmp_path / "state")
    assert result.sessions_written == ("2026-01-05",)
    assert any("unreadable" in why for _, why in result.skipped)


def test_an_interrupted_import_leaves_no_partial_session(tmp_path: Path) -> None:
    """A half-written session would later read as a real, short recording."""
    source = tmp_path / "ticks"
    _write(source, ticks=0, settle=None)  # no ticks at all
    result = import_directory(source, tmp_path / "state")
    assert result.snapshots_written == 0
    assert not (tmp_path / "state" / "market" / f"{SESSION}.jsonl").exists()
    assert not list((tmp_path / "state" / "market").glob("*.partial"))


# --------------------------------------------------------------------------- #
# End to end: imported data trains                                            #
# --------------------------------------------------------------------------- #
def test_imported_recordings_produce_labeled_training_observations(
    tmp_path: Path,
) -> None:
    """The whole reason for this module: real recordings become training rows."""
    from spy_der.training.observations import build_observations

    source = tmp_path / "ticks"
    for day in range(5, 10):
        _write(source, ticks=8, session=f"2026-01-{day:02d}")
    import_directory(source, tmp_path / "state")

    observations = build_observations(tmp_path / "state")
    assert len(observations) > 0
    assert len(observations.sessions) == 5
    rows, y, sessions = observations.target("up_30m")
    assert rows and len(rows) == len(y) == len(sessions)


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def test_cli_imports_and_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from spy_der.runtime.zerodte_import import main

    _write(tmp_path / "ticks")
    code = main(
        [
            "--source", str(tmp_path / "ticks"),
            "--state-root", str(tmp_path / "state"),
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["snapshots_written"] == 6
    assert payload["sessions_written"] == [SESSION]


def test_cli_exits_two_when_the_source_is_missing(tmp_path: Path) -> None:
    from spy_der.runtime.zerodte_import import main

    assert main(["--source", str(tmp_path / "nope"), "--state-root", str(tmp_path)]) == 2


def test_cli_requires_an_explicit_source() -> None:
    """No default: SPY-DER must not assume a legacy 0DTE path exists."""
    from spy_der.runtime.zerodte_import import build_arg_parser

    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(["--state-root", "/tmp/whatever"])


def test_cli_exits_two_when_the_source_holds_no_recordings(tmp_path: Path) -> None:
    from spy_der.runtime.zerodte_import import main

    (tmp_path / "ticks").mkdir()
    assert main(["--source", str(tmp_path / "ticks"), "--state-root", str(tmp_path)]) == 2


# --------------------------------------------------------------------------- #
# --overwrite must not destroy live recordings                                #
# --------------------------------------------------------------------------- #
def test_overwrite_refuses_a_session_spy_der_recorded_live(tmp_path: Path) -> None:
    """The handover footgun: import, collect for weeks, then re-import.

    `--overwrite` is for re-importing a session this tool produced. Once the
    market service is collecting, replacing the file would silently and
    irreversibly delete live history.
    """
    from datetime import date as _date

    from spy_der.contracts.market import CanonicalMarketSnapshot, SessionStatus
    from spy_der.market_data.recording import build_record

    _write(tmp_path / "ticks")
    import_directory(tmp_path / "ticks", tmp_path / "state")
    recording = tmp_path / "state" / "market" / f"{SESSION}.jsonl"

    # The market service appends a live snapshot (no import flag).
    from decimal import Decimal as _Decimal

    live = CanonicalMarketSnapshot(
        snapshot_id="live-1",
        content_hash="sha256:live-1",
        timestamp=OPEN_UTC,
        session_date=_date(2026, 1, 5),
        underlying_symbol="SPY",
        underlying_price=_Decimal("500"),
        session_status=SessionStatus.OPEN,
    )
    with recording.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(build_record(6, live), sort_keys=True) + "\n")

    before = recording.read_text()
    result = import_directory(tmp_path / "ticks", tmp_path / "state", overwrite=True)

    assert recording.read_text() == before, "live records were destroyed"
    assert result.snapshots_written == 0
    assert any("live records" in why for _, why in result.skipped)


def test_overwrite_still_works_on_a_purely_imported_session(tmp_path: Path) -> None:
    """Re-importing this tool's own output stays cheap and allowed."""
    _write(tmp_path / "ticks")
    import_directory(tmp_path / "ticks", tmp_path / "state")
    result = import_directory(tmp_path / "ticks", tmp_path / "state", overwrite=True)
    assert result.snapshots_written == 6


def test_an_unreadable_target_is_treated_as_live_not_as_safe(tmp_path: Path) -> None:
    """Unreadable is not proof of safety; refuse rather than guess."""
    _write(tmp_path / "ticks")
    market = tmp_path / "state" / "market"
    market.mkdir(parents=True)
    (market / f"{SESSION}.jsonl").write_text("not json at all\n", encoding="utf-8")

    result = import_directory(tmp_path / "ticks", tmp_path / "state", overwrite=True)
    assert result.snapshots_written == 0
    assert any("live records" in why for _, why in result.skipped)


def test_one_refused_session_does_not_abort_the_rest(tmp_path: Path) -> None:
    _write(tmp_path / "ticks", session="2026-01-05")
    _write(tmp_path / "ticks", session="2026-01-06")
    market = tmp_path / "state" / "market"
    market.mkdir(parents=True)
    (market / "2026-01-05.jsonl").write_text("garbage\n", encoding="utf-8")

    result = import_directory(tmp_path / "ticks", tmp_path / "state", overwrite=True)
    assert result.sessions_written == ("2026-01-06",)
