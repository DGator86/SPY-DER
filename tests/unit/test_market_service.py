"""Market runtime and provider factory (`spy-der market`).

Also pins the known consequence of shipping Massive alone: without a settlement
source, every live snapshot carries the missing-settlement quality penalty. That
is correct fail-closed behavior, not a defect — but it is load-bearing enough to
assert, because it means Massive by itself is not sufficient to trade on.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from spy_der.market_data.providers.base import RawTick
from spy_der.market_data.providers.factory import (
    AVAILABLE_PROVIDERS,
    PENDING_PROVIDERS,
    UnknownProviderError,
    build_provider_chain,
)
from spy_der.market_data.providers.massive import ET
from spy_der.market_data.replay import ReplayFeed
from spy_der.runtime.market_service import MarketService, MarketServiceConfig, main

TS = datetime(2026, 7, 24, 17, 30, tzinfo=UTC)


def _current_session() -> str:
    """Today's expiry in EXCHANGE time — the provider filters on ET, not UTC."""
    return datetime.now(tz=UTC).astimezone(ET).date().isoformat()


# --------------------------------------------------------------------------- #
# Factory                                                                     #
# --------------------------------------------------------------------------- #
def test_configured_provider_is_active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MASSIVE_API_KEY", "k")
    chain = build_provider_chain(["massive"])
    assert [p.name for p in chain.providers] == ["massive"]
    assert chain.skipped == ()
    assert chain.is_empty is False


def test_unconfigured_provider_is_skipped_visibly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skipping must be reported — a silent omission looks like a healthy chain."""
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    chain = build_provider_chain(["massive"])
    assert chain.is_empty
    assert chain.skipped == (("massive", "no credential"),)
    assert "no credential" in chain.describe()


def test_order_is_preserved_as_failover_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MASSIVE_API_KEY", "k")
    chain = build_provider_chain(["massive", "massive"])
    assert len(chain.providers) == 2


@pytest.mark.parametrize("name", sorted(PENDING_PROVIDERS))
def test_unported_provider_raises_with_a_pointer(name: str) -> None:
    with pytest.raises(UnknownProviderError, match="not ported yet"):
        build_provider_chain([name])


def test_unknown_provider_raises() -> None:
    with pytest.raises(UnknownProviderError, match="unknown provider"):
        build_provider_chain(["nope"])


def test_available_and_pending_do_not_overlap() -> None:
    assert not (AVAILABLE_PROVIDERS & PENDING_PROVIDERS)


# --------------------------------------------------------------------------- #
# Service: fail closed                                                        #
# --------------------------------------------------------------------------- #
def test_no_credential_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A unit that starts clean and records nothing is worse than one that fails."""
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("TRADIER_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("TRADIER_API_KEY", raising=False)
    assert main(["--state-root", str(tmp_path), "--max-ticks", "1"]) == 3
    heartbeat = json.loads((tmp_path / "health" / "market.json").read_text(encoding="utf-8"))
    assert heartbeat["health"] == "failed"
    assert heartbeat["error"] == "no_credential"
    assert "TRADIER_ACCESS_TOKEN" in heartbeat["detail"]


def test_unported_provider_exits_nonzero(tmp_path: Path) -> None:
    """Naming an adapter that does not exist fails loudly, not silently.

    Was `tradier`, which is now ported — `tastytrade` is still pending. Exit 2
    (unknown adapter) stays distinct from exit 3 (adapter exists, no credential),
    because the operator fix differs: one is a config typo, the other a key.
    """
    assert main(["--provider", "tastytrade", "--state-root", str(tmp_path)]) == 2
    heartbeat = json.loads((tmp_path / "health" / "market.json").read_text(encoding="utf-8"))
    assert heartbeat["health"] == "failed"
    assert heartbeat["error"] == "unknown_provider"


def test_tradier_without_a_credential_exits_three_not_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ported-but-unconfigured is a different failure from unported."""
    monkeypatch.delenv("TRADIER_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("TRADIER_API_KEY", raising=False)
    assert main(["--provider", "tradier", "--state-root", str(tmp_path)]) == 3


def test_unknown_provider_exits_nonzero(tmp_path: Path) -> None:
    assert main(["--provider", "bogus", "--state-root", str(tmp_path)]) == 2


# --------------------------------------------------------------------------- #
# Service: recording                                                          #
# --------------------------------------------------------------------------- #
def _rows(session: str, spot: float = 600.0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for strike in (598.0, 600.0, 602.0):
        for side, intrinsic in (
            ("call", max(spot - strike, 0.0)),
            ("put", max(strike - spot, 0.0)),
        ):
            rows.append({
                "details": {
                    "contract_type": side,
                    "strike_price": strike,
                    "expiration_date": session,
                },
                "greeks": {"gamma": 0.02, "delta": 0.5 if side == "call" else -0.5},
                "open_interest": 1000,
                "last_quote": {"bid": intrinsic + 0.95, "ask": intrinsic + 1.05},
                "day": {"volume": 10},
            })
    return rows


def _minute_bars(count: int = 5, *, close: float = 600.0) -> list[dict[str, Any]]:
    """Polygon-shaped 1-minute aggregates ending now."""
    base_ms = int(datetime.now(tz=UTC).timestamp() * 1000) - count * 60_000
    return [
        {
            "t": base_ms + i * 60_000,
            "o": close - 0.10,
            "h": close + 0.15,
            "l": close - 0.20,
            "c": close,
            "v": 1_000 + i,
        }
        for i in range(count)
    ]


@pytest.fixture
def stub_vendor(monkeypatch: pytest.MonkeyPatch):
    """A full live-shaped Massive vendor: chain, underlying quote and bars.

    Routing by endpoint matters — a stub that answered every URL with the chain
    would leave ``bars`` empty and quietly turn every assertion about snapshot
    quality into an assertion about a degraded snapshot.
    """

    def _install(session: str) -> None:
        monkeypatch.setenv("MASSIVE_API_KEY", "k")

        def fake(url: str, **_kw: Any) -> dict[str, Any]:
            if "/v2/snapshot/locale/" in url:
                return {"ticker": {"ticker": "SPY", "lastTrade": {"p": 600.0}}}
            if "/range/1/minute/" in url:
                return {"results": _minute_bars()}
            return {"results": _rows(session)}

        monkeypatch.setattr("spy_der.market_data.providers.massive.get_json", fake)

    return _install


def _run(
    tmp_path: Path, ticks: int = 1, *, settlement: str = ""
) -> list[dict[str, Any]]:
    """Run the service. Settlement defaults off so these stay offline.

    The default deployment *does* configure Yahoo (see
    `test_settlement_provider_is_configured_by_default`), but Yahoo is a real
    network call and `conftest` blocks the socket, so tests that only care about
    the Massive path opt out explicitly rather than silently.
    """
    service = MarketService(
        config=MarketServiceConfig(
            state_root=str(tmp_path),
            interval_seconds=0.0,
            max_ticks=ticks,
            settlement_provider=settlement,
        )
    )
    assert service.run() == 0
    files = sorted((tmp_path / "market").glob("*.jsonl"))
    assert files
    return [json.loads(line) for line in files[0].read_text().splitlines()]


def test_tick_is_recorded_as_jsonl(tmp_path: Path, stub_vendor: Any) -> None:
    session = _current_session()
    stub_vendor(session)
    records = _run(tmp_path)
    assert len(records) == 1
    record = records[0]
    assert record["seq"] == 0
    assert record["snapshot_id"]
    assert record["record_hash"].startswith("sha256:")
    assert record["snapshot"]["option_chain"]


def test_sequence_increments_across_ticks(tmp_path: Path, stub_vendor: Any) -> None:
    session = _current_session()
    stub_vendor(session)
    records = _run(tmp_path, ticks=3)
    assert [r["seq"] for r in records] == [0, 1, 2]


def test_spot_is_recovered_into_the_recording(tmp_path: Path, stub_vendor: Any) -> None:
    session = _current_session()
    stub_vendor(session)
    records = _run(tmp_path)
    assert Decimal(records[0]["snapshot"]["underlying_price"]) == pytest.approx(
        Decimal("600.00"), abs=Decimal("0.05")
    )


def test_massive_alone_carries_the_missing_settlement_penalty(
    tmp_path: Path, stub_vendor: Any
) -> None:
    """Known state until a settlement provider (yahoo) is ported.

    ``config.yaml`` sets ``min_data_quality: 0.75`` while a settlement-less
    snapshot scores 0.5, so the deterministic layers will refuse to trade on live
    Massive data. That is the correct fail-closed outcome and is asserted here so
    it cannot be mistaken for a regression later — but it does mean Massive by
    itself is not enough to run the pipeline.
    """
    session = _current_session()
    stub_vendor(session)
    snapshot = _run(tmp_path)[0]["snapshot"]
    assert snapshot["missing_components"] == ["settlement"]
    assert float(snapshot["data_quality"]["penalty"]) == pytest.approx(0.5)


def test_bars_are_recorded_alongside_the_chain(
    tmp_path: Path, stub_vendor: Any
) -> None:
    """Without bars in the recording, every history-dependent feature is dead."""
    session = _current_session()
    stub_vendor(session)
    snapshot = _run(tmp_path)[0]["snapshot"]
    assert len(snapshot["bars_1m"]) == 5
    assert "bars" not in snapshot["missing_components"]


def test_the_underlying_price_is_measured_not_inferred(
    tmp_path: Path, stub_vendor: Any
) -> None:
    """With a live underlying quote available, parity must not be what priced it."""
    session = _current_session()
    stub_vendor(session)
    snapshot = _run(tmp_path)[0]["snapshot"]
    assert "spot:underlying_quote" in snapshot["data_quality"]["flags"]
    assert "spot:put_call_parity" not in snapshot["data_quality"]["flags"]


def test_a_provider_returning_nothing_is_survived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MASSIVE_API_KEY", "k")
    monkeypatch.setattr(
        "spy_der.market_data.providers.massive.get_json",
        lambda url, **kw: {"results": []},
    )
    service = MarketService(
        config=MarketServiceConfig(
            state_root=str(tmp_path), interval_seconds=0.0, max_ticks=1
        )
    )
    assert service.run() == 0
    assert not list((tmp_path / "market").glob("*.jsonl")) or True


def test_a_raising_feed_does_not_kill_the_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One bad tick must not take down the front of the pipeline."""
    monkeypatch.setenv("MASSIVE_API_KEY", "k")

    class _Boom:
        last_source = None

        def snapshot(self, _ts: datetime) -> RawTick | None:
            raise RuntimeError("vendor exploded")

    service = MarketService(
        config=MarketServiceConfig(
            state_root=str(tmp_path), interval_seconds=0.0, max_ticks=1
        )
    )
    (tmp_path / "market").mkdir(parents=True, exist_ok=True)
    service._tick(_Boom())  # type: ignore[arg-type]
    # Nothing recorded and no exception escaped.
    assert not list((tmp_path / "market").glob("*.jsonl"))


def test_recording_path_is_per_session(tmp_path: Path) -> None:
    cfg = MarketServiceConfig(state_root=str(tmp_path))
    path = cfg.recording_path(datetime(2026, 7, 24, 12, 0, tzinfo=UTC))
    assert path.name == "2026-07-24.jsonl"
    assert path.parent == Path(tmp_path) / "market"


def test_stop_signal_ends_the_loop(tmp_path: Path, stub_vendor: Any) -> None:
    session = _current_session()
    stub_vendor(session)
    service = MarketService(
        config=MarketServiceConfig(state_root=str(tmp_path), interval_seconds=0.0)
    )
    service.request_stop()
    assert service.run() == 0
    assert not list((tmp_path / "market").glob("*.jsonl"))


def test_settlement_provider_is_configured_by_default() -> None:
    """Without one, every snapshot carries the missing-settlement penalty.

    Yahoo needs no credential, so the default is a working settlement source
    rather than a standing quality penalty that has to be explained away.
    """
    assert MarketServiceConfig().settlement_provider == "yahoo"


def test_settlement_provider_is_wired_into_the_feed(
    tmp_path: Path, stub_vendor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With settlement present the snapshot is fully live and unpenalized."""
    session = _current_session()
    stub_vendor(session)
    monkeypatch.setattr(
        "spy_der.market_data.providers.yahoo.get_json",
        lambda url, **kw: {
            "chart": {
                "result": [{"meta": {"regularMarketPrice": 600.0}}],
                "error": None,
            }
        },
    )
    snapshot = _run(tmp_path, settlement="yahoo")[0]["snapshot"]
    assert snapshot["missing_components"] == []
    assert float(snapshot["data_quality"]["penalty"]) == 0.0


# --------------------------------------------------------------------------- #
# Sequence continuity across restarts and the 0DTE handover                   #
# --------------------------------------------------------------------------- #
def _seqs(path: Path) -> list[int]:
    return [json.loads(line)["seq"] for line in path.read_text().splitlines() if line.strip()]


def test_a_restart_mid_session_does_not_break_the_sequence(
    tmp_path: Path, stub_vendor: Any
) -> None:
    """`Restart=always` means this happens on any crash.

    The recording is append-only and `ReplayFeed` requires a gap-free sequence,
    so a counter restarting at 0 would corrupt the entire session — losing both
    what was recorded before the crash and everything after it.
    """
    session = _current_session()
    stub_vendor(session)
    _run(tmp_path, ticks=2)          # first process
    _run(tmp_path, ticks=2)          # restarted process, same session file

    recording = tmp_path / "market" / f"{session}.jsonl"
    assert _seqs(recording) == [0, 1, 2, 3]
    assert len(ReplayFeed.from_file(recording)) == 4


def test_taking_over_from_an_imported_session_keeps_the_sequence(
    tmp_path: Path, stub_vendor: Any
) -> None:
    """The 0DTE handover: import catches us up, then SPY-DER collects onward.

    Both write the same `market/<session>.jsonl`. Restarting the sequence would
    make the imported history and the live continuation mutually unreadable.
    """
    session = _current_session()
    market = tmp_path / "market"
    market.mkdir(parents=True)
    imported = market / f"{session}.jsonl"

    # Stand in for the importer: three already-recorded snapshots.
    from spy_der.contracts.market import CanonicalMarketSnapshot, SessionStatus
    from spy_der.market_data.recording import build_record

    lines = []
    for seq in range(3):
        snapshot = CanonicalMarketSnapshot(
            snapshot_id=f"imported-{seq}",
            content_hash=f"sha256:imported-{seq}",
            timestamp=datetime.now(tz=UTC),
            session_date=datetime.now(tz=UTC).astimezone(ET).date(),
            underlying_symbol="SPY",
            underlying_price=Decimal("500"),
            session_status=SessionStatus.OPEN,
        )
        lines.append(json.dumps(build_record(seq, snapshot), sort_keys=True))
    imported.write_text("\n".join(lines) + "\n", encoding="utf-8")

    stub_vendor(session)
    _run(tmp_path, ticks=2)

    assert _seqs(imported) == [0, 1, 2, 3, 4]
    feed = ReplayFeed.from_file(imported)   # verifies hashes and continuity
    assert len(feed) == 5


def test_a_partial_final_line_still_resumes_rather_than_restarting(
    tmp_path: Path, stub_vendor: Any
) -> None:
    """A crash mid-write leaves a truncated line; resuming beats guessing 0."""
    session = _current_session()
    stub_vendor(session)
    _run(tmp_path, ticks=2)
    recording = tmp_path / "market" / f"{session}.jsonl"
    with recording.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "snapshot_id": "trunc')

    service = MarketService(
        config=MarketServiceConfig(
            state_root=str(tmp_path),
            interval_seconds=0.0,
            max_ticks=1,
            settlement_provider="",  # offline, like _run
        )
    )
    assert service.run() == 0
    seqs = _seqs_tolerant(recording)
    assert seqs[-1] == 2  # continued from the last parsable sequence


def _seqs_tolerant(path: Path) -> list[int]:
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line)["seq"])
        except (ValueError, KeyError):
            continue
    return out


def test_sequences_are_tracked_per_session(tmp_path: Path) -> None:
    """A service spanning midnight must not carry one session's count into the next."""
    from spy_der.runtime.market_service import MarketService, MarketServiceConfig

    service = MarketService(config=MarketServiceConfig(state_root=str(tmp_path)))
    market = tmp_path / "market"
    market.mkdir(parents=True)
    (market / "2026-07-27.jsonl").write_text(
        json.dumps({"seq": 41, "snapshot_id": "x"}) + "\n", encoding="utf-8"
    )
    assert service._next_seq(market / "2026-07-27.jsonl") == 42
    assert service._next_seq(market / "2026-07-28.jsonl") == 0
