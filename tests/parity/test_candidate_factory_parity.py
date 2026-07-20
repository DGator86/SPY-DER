"""Phase 7 candidate-factory parity (master spec §31, §32, §65)."""

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

_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = _ROOT / "baseline" / "expected_outputs" / "phase7" / "candidate_universe.json"


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
        snapshot_id="snap-phase7-parity",
        content_hash="sha256:parity",
        timestamp=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        session_date=date(2026, 1, 5),
        underlying_symbol="SPY",
        underlying_price=Decimal("100"),
        session_status=SessionStatus.OPEN,
        option_chain=tuple(chain),
        minutes_to_close=90,
    )


def test_candidate_universe_parity() -> None:
    universe = generate_candidate_universe(
        _snapshot(),
        cfg=FactoryConfig(
            families=frozenset(
                {
                    CandidateFamily.BULL_PUT_CREDIT_SPREAD.value,
                    CandidateFamily.BEAR_CALL_CREDIT_SPREAD.value,
                    CandidateFamily.CALL_DEBIT_SPREAD.value,
                    CandidateFamily.LONG_CALL.value,
                    CandidateFamily.IRON_BUTTERFLY.value,
                }
            ),
            spread_widths=(Decimal("1"), Decimal("2")),
        ),
    )
    payload = {
        "universe_id": universe.universe_id,
        "snapshot_id": universe.snapshot_id,
        "factory_version": universe.factory_version,
        "candidate_count": len(universe.candidates),
        "candidates": [
            {
                "candidate_id": c.candidate_id,
                "family": c.family,
                "direction": c.direction,
                "entry_type": c.entry_type.value,
                "maximum_loss": str(c.maximum_loss),
                "maximum_profit": None if c.maximum_profit is None else str(c.maximum_profit),
                "capital_required": str(c.capital_required),
                "entry_credit": str(c.entry_credit),
                "geometry_hash": c.geometry_hash,
                "terminal_payoff_hash": c.terminal_payoff_hash,
                "legs": [
                    {
                        "option_type": leg.option_type.value,
                        "strike": str(leg.strike),
                        "quantity": leg.quantity,
                        "contract_id": leg.contract_id,
                    }
                    for leg in c.legs
                ],
            }
            for c in universe.candidates
        ],
    }
    actual = json.loads(to_canonical_json(payload))
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert actual == expected
