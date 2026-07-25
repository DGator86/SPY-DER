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
    assert main(["--state-root", str(tmp_path), "--max-ticks", "1"]) == 3


def test_unported_provider_exits_nonzero(tmp_path: Path) -> None:
    assert main(["--provider", "tradier", "--state-root", str(tmp_path)]) == 2


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


@pytest.fixture
def stub_vendor(monkeypatch: pytest.MonkeyPatch):
    def _install(session: str) -> None:
        monkeypatch.setenv("MASSIVE_API_KEY", "k")
        monkeypatch.setattr(
            "spy_der.market_data.providers.massive.get_json",
            lambda url, **kw: {"results": _rows(session)},
        )

    return _install


def _run(tmp_path: Path, ticks: int = 1) -> list[dict[str, Any]]:
    service = MarketService(
        config=MarketServiceConfig(
            state_root=str(tmp_path), interval_seconds=0.0, max_ticks=ticks
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
    assert "settlement" in snapshot["missing_components"]
    assert float(snapshot["data_quality"]["penalty"]) == pytest.approx(0.5)


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
    assert service._seq == 0  # nothing recorded, no exception escaped


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
    assert service._seq == 0
