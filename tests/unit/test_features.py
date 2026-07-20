from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from spy_der.contracts.market import OptionContract, OptionQuote, OptionType
from spy_der.features import (
    GexRankWindow,
    StructuralStateService,
    compute_oi_gex,
    compute_rnd,
    compute_volatility,
)
from spy_der.market_data import CanonicalSnapshotAssembler

ET = ZoneInfo("America/New_York")
TS = datetime(2026, 1, 5, 14, 30, tzinfo=ET)
EXPIRY = date(2026, 1, 5)
SPOT = Decimal("500")


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_call(spot: float, strike: float, sigma: float, t: float) -> float:
    if t <= 0 or sigma <= 0:
        return max(0.0, spot - strike)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    return spot * _norm_cdf(d1) - strike * _norm_cdf(d2)


def _bs_gamma(spot: float, strike: float, sigma: float, t: float) -> float:
    if t <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    return pdf / (spot * sigma * math.sqrt(t))


def _quote(strike: int, kind: OptionType, *, mid: float, gamma: float, oi: int) -> OptionQuote:
    contract = OptionContract(
        contract_id=f"SPY-{strike}-{kind.value}",
        underlying_symbol="SPY",
        expiration=EXPIRY,
        option_type=kind,
        strike=Decimal(strike),
    )
    price = Decimal(str(round(mid, 4)))
    return OptionQuote(
        contract=contract,
        received_at=TS,
        source="test",
        bid=price,
        ask=price,
        gamma=gamma,
        open_interest=oi,
    )


def _chain(sigma: float = 0.02, t: float = 0.02) -> tuple[OptionQuote, ...]:
    spot = float(SPOT)
    quotes: list[OptionQuote] = []
    for strike in range(480, 521, 2):
        call_mid = _bs_call(spot, strike, sigma, t)
        put_mid = call_mid - (spot - strike)  # put-call parity, r=0
        gamma = _bs_gamma(spot, strike, sigma, t)
        quotes.append(_quote(strike, OptionType.CALL, mid=call_mid, gamma=gamma, oi=1000))
        quotes.append(_quote(strike, OptionType.PUT, mid=max(put_mid, 0.0), gamma=gamma, oi=1000))
    return tuple(quotes)


def _snapshot(chain: tuple[OptionQuote, ...]):
    return CanonicalSnapshotAssembler().assemble(
        timestamp=TS,
        underlying_symbol="SPY",
        underlying_price=SPOT,
        option_chain=chain,
    )


# -------------------------------------------------------------------- GEX ----
def test_gex_levels_computed() -> None:
    # Constant gamma so dollar-gamma tracks OI: concentrate call OI at 510 and
    # put OI at 490 to pin the walls there deterministically.
    quotes: list[OptionQuote] = []
    for strike in range(480, 521, 2):
        call_oi = 50000 if strike == 510 else 1000
        put_oi = 50000 if strike == 490 else 1000
        quotes.append(_quote(strike, OptionType.CALL, mid=1.0, gamma=0.05, oi=call_oi))
        quotes.append(_quote(strike, OptionType.PUT, mid=1.0, gamma=0.05, oi=put_oi))
    gex = compute_oi_gex(_snapshot(tuple(quotes)))
    assert gex is not None
    assert gex.call_wall == Decimal("510")  # strike >= spot with max call gamma
    assert gex.put_wall == Decimal("490")  # strike <= spot with max put gamma
    assert gex.n_strikes == 21
    assert -1 <= gex.net_ratio <= 1
    assert gex.gamma_sign in (-1, 0, 1)


def test_gex_none_without_greeks() -> None:
    contract = OptionContract(
        contract_id="SPY-500-CALL",
        underlying_symbol="SPY",
        expiration=EXPIRY,
        option_type=OptionType.CALL,
        strike=Decimal("500"),
    )
    quote = OptionQuote(contract=contract, received_at=TS, source="test")  # no gamma/OI
    assert compute_oi_gex(_snapshot((quote,))) is None


# ------------------------------------------------------------- volatility ----
def test_volatility_summary() -> None:
    vol = compute_volatility(_snapshot(_chain()), session_open_price=Decimal("498"))
    assert vol is not None
    assert vol.atm_strike == Decimal("500")
    assert vol.atm_straddle > 0
    assert vol.expected_move_pct > 0
    assert vol.expected_move_consumed is not None


# -------------------------------------------------------------------- RND ----
def test_rnd_recovers_lognormal_sigma() -> None:
    sigma, t = 0.02, 0.02
    rnd = compute_rnd(_snapshot(_chain(sigma=sigma, t=t)))
    assert rnd is not None
    assert rnd.normalized
    # Risk-neutral std of S_T ~ spot * sigma * sqrt(t) for small moves.
    theoretical = float(SPOT) * sigma * math.sqrt(t)
    assert rnd.std == abs(rnd.std)
    assert math.isclose(rnd.std, theoretical, rel_tol=0.25)
    assert math.isclose(rnd.mean, float(SPOT), rel_tol=0.02)
    assert 0.3 <= rnd.prob_below_spot <= 0.7


def test_rnd_none_when_too_few_strikes() -> None:
    chain = tuple(q for q in _chain() if q.contract.strike in {Decimal("500"), Decimal("502")})
    assert compute_rnd(_snapshot(chain)) is None


# ------------------------------------------------------ structural service ----
def test_structural_state_service() -> None:
    snap = _snapshot(_chain())
    state = StructuralStateService().build(snap, session_open_price=Decimal("499"))
    assert state.snapshot_id == snap.snapshot_id
    assert state.gex_oi is not None
    assert state.volatility is not None
    assert state.rnd is not None
    assert "pin_score" in state.missing_fields  # history-dependent field flagged


def test_structural_state_deterministic_id() -> None:
    snap = _snapshot(_chain())
    a = StructuralStateService().build(snap)
    b = StructuralStateService().build(snap)
    assert a.structural_state_id == b.structural_state_id


# ------------------------------------------------- adaptive rank window ----
def test_gex_rank_window_neutral_until_warm(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = str(tmp_path / "gex.json")
    window = GexRankWindow(path=path, min_samples=5)
    for i in range(4):
        assert window.rank(1e9 * (i + 1), now_epoch=1000.0 + i) == 0.5  # not warm yet
    assert not window.is_warm
    window.rank(5e9, now_epoch=1005.0)
    assert window.is_warm


def test_gex_rank_window_persists_across_restart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = str(tmp_path / "gex.json")
    first = GexRankWindow(path=path, min_samples=3)
    for i in range(5):
        first.rank(float(i + 1) * 1e9, now_epoch=2000.0 + i)
    # New instance loads persisted history and stays warm.
    second = GexRankWindow(path=path, min_samples=3)
    assert len(second) == 5
    assert second.is_warm
    high_rank = second.rank(100e9, now_epoch=2100.0)
    assert high_rank > 0.5  # a large |GEX| ranks above the persisted history
