"""Phase 8 executable-economics parity (master spec §33, §65)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from spy_der.candidates import FactoryConfig, generate_candidate_universe
from spy_der.contracts import (
    CandidateFamily,
    CanonicalMarketSnapshot,
    OptionContract,
    OptionQuote,
    OptionType,
    SessionStatus,
    to_canonical_json,
)
from spy_der.economics import calculate_universe_economics

_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = _ROOT / "baseline" / "expected_outputs" / "phase8" / "candidate_economics.json"


def _quote(strike: str, option_type: OptionType, bid: str, ask: str) -> OptionQuote:
    exp = date(2026, 1, 5)
    right = "C" if option_type is OptionType.CALL else "P"
    sid = f"SPY{exp:%y%m%d}{right}{int(Decimal(strike) * 1000):08d}"
    return OptionQuote(
        contract=OptionContract(
            contract_id=sid,
            underlying_symbol="SPY",
            expiration=exp,
            option_type=option_type,
            strike=Decimal(strike),
        ),
        received_at=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        source="parity",
        bid=Decimal(bid),
        ask=Decimal(ask),
        mark=(Decimal(bid) + Decimal(ask)) / Decimal("2"),
        age_seconds=1.0,
    )


def _snapshot() -> CanonicalMarketSnapshot:
    chain: list[OptionQuote] = []
    for k in [str(x) for x in range(97, 104)]:
        dist = abs(Decimal(k) - Decimal("100"))
        mid = max(Decimal("0.15"), Decimal("4") - dist * Decimal("0.5"))
        half = Decimal("0.05")
        bid, ask = str(mid - half), str(mid + half)
        chain.append(_quote(k, OptionType.CALL, bid, ask))
        chain.append(_quote(k, OptionType.PUT, bid, ask))
    return CanonicalMarketSnapshot(
        snapshot_id="snap-phase8-parity",
        content_hash="sha256:p8",
        timestamp=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        session_date=date(2026, 1, 5),
        underlying_symbol="SPY",
        underlying_price=Decimal("100"),
        session_status=SessionStatus.OPEN,
        option_chain=tuple(chain),
        minutes_to_close=90,
    )


def test_candidate_economics_parity() -> None:
    snap = _snapshot()
    universe = generate_candidate_universe(
        snap,
        cfg=FactoryConfig(
            families=frozenset(
                {
                    CandidateFamily.BULL_PUT_CREDIT_SPREAD.value,
                    CandidateFamily.LONG_CALL.value,
                }
            ),
            spread_widths=(Decimal("1"), Decimal("2")),
        ),
    )
    panel = calculate_universe_economics(universe, snap)
    payload = {
        "snapshot_id": snap.snapshot_id,
        "candidate_count": len(panel),
        "economics": [
            {
                "candidate_id": e.candidate_id,
                "mid_price": None if e.mid_price is None else str(e.mid_price),
                "natural_price": None if e.natural_price is None else str(e.natural_price),
                "expected_fill_price": (
                    None if e.expected_fill_price is None else str(e.expected_fill_price)
                ),
                "conservative_fill_price": (
                    None if e.conservative_fill_price is None else str(e.conservative_fill_price)
                ),
                "fill_probability": e.fill_probability,
                "expected_fill_fraction": e.expected_fill_fraction,
                "fees": str(e.fees),
                "entry_slippage": str(e.entry_slippage),
                "exit_slippage": str(e.exit_slippage),
                "stop_slippage": str(e.stop_slippage),
                "fallback_level": e.fallback_level,
                "economics_version": e.economics_version,
            }
            for e in panel
        ],
    }
    actual = json.loads(to_canonical_json(payload))
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert actual == expected
