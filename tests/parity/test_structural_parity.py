"""Phase 3 structural-state parity fixture (master spec §17-§22, §65).

A fixed synthetic option chain (Black-Scholes priced, sigma=0.03, t=0.05) is run
through the structural-state service and must reproduce a frozen canonical
`StructuralState` (`baseline/expected_outputs/phase3/`) bit-for-bit, plus a
frozen structural-state id. This locks the GEX / volatility / RND feature
contract so later phases cannot silently drift it.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from spy_der.contracts import to_canonical_json
from spy_der.contracts.market import OptionContract, OptionQuote, OptionType
from spy_der.features import StructuralStateService
from spy_der.market_data import CanonicalSnapshotAssembler

_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = _ROOT / "baseline" / "expected_outputs" / "phase3" / "structural_state.json"

ET = ZoneInfo("America/New_York")
TS = datetime(2026, 1, 5, 14, 30, tzinfo=ET)
EXPIRY = date(2026, 1, 5)
SPOT = Decimal("500")
_SIGMA, _T = 0.03, 0.05
_EXPECTED_STATE_ID = "struct-72bacd101148"


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_call(spot: float, strike: float, sigma: float, t: float) -> float:
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    return spot * _ncdf(d1) - strike * _ncdf(d2)


def _bs_gamma(spot: float, strike: float, sigma: float, t: float) -> float:
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    return pdf / (spot * sigma * math.sqrt(t))


def _quote(strike: int, kind: OptionType, mid: float, gamma: float) -> OptionQuote:
    contract = OptionContract(
        contract_id=f"SPY-{strike}-{kind.value}",
        underlying_symbol="SPY",
        expiration=EXPIRY,
        option_type=kind,
        strike=Decimal(strike),
    )
    price = Decimal(str(round(mid, 4)))
    return OptionQuote(
        contract=contract, received_at=TS, source="fixture",
        bid=price, ask=price, gamma=gamma, open_interest=1000,
    )


def _state() -> object:
    spot = float(SPOT)
    quotes: list[OptionQuote] = []
    for strike in range(470, 531, 2):
        call_mid = _bs_call(spot, strike, _SIGMA, _T)
        put_mid = call_mid - (spot - strike)
        gamma = _bs_gamma(spot, strike, _SIGMA, _T)
        quotes.append(_quote(strike, OptionType.CALL, call_mid, gamma))
        quotes.append(_quote(strike, OptionType.PUT, max(put_mid, 0.0), gamma))
    snapshot = CanonicalSnapshotAssembler().assemble(
        timestamp=TS, underlying_symbol="SPY", underlying_price=SPOT,
        option_chain=tuple(quotes),
    )
    return StructuralStateService().build(snapshot, session_open_price=Decimal("498"))


def test_structural_state_matches_golden() -> None:
    produced = json.loads(to_canonical_json(_state()))
    expected = json.loads(_EXPECTED.read_text())
    assert produced == expected


def test_structural_state_id_is_frozen() -> None:
    assert _state().structural_state_id == _EXPECTED_STATE_ID  # type: ignore[attr-defined]
