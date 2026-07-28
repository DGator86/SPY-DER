"""The Dojo learns from SPY-DER's own recordings.

The gap this covers was silent and complete. `spy-der dojo` exited 0 and wrote
a report while every phase said `no MarketExperienceProvider` — because the
only provider that existed read a 0DTE handoff format nothing in SPY-DER
writes. Recordings were accumulating under `<state-root>/market` from the
import and from the live service, and the learning loop could not see any of
them.

The headline test is `test_the_dojo_scores_a_recorded_session`: it records
sessions the way the market service does, hands the Dojo nothing but a state
root, and asserts the recorded phase actually scored trades. That test fails
against the old code for the right reason — no provider, no score.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np

from spy_der.candidates.factory import generate_candidate_universe
from spy_der.candidates.payoff import intrinsic
from spy_der.contracts.market import (
    Bar,
    CanonicalMarketSnapshot,
    DataQuality,
    OptionContract,
    OptionQuote,
    OptionType,
    SessionStatus,
)
from spy_der.dojo.authority import default_authorities
from spy_der.dojo.config import DojoConfig
from spy_der.dojo.native_tape import NativeTapeProvider
from spy_der.dojo.recorded import run_recorded_phase
from spy_der.market_data.recording import build_record

ET_OPEN_UTC = 14  # 09:30 ET expressed in UTC during standard time
FULL_SESSION_BARS = 390


def _bars(session: date, n: int, *, seed: int) -> tuple[Bar, ...]:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 0.35, n).cumsum()
    start = datetime(session.year, session.month, session.day, ET_OPEN_UTC, 30, tzinfo=UTC)
    out: list[Bar] = []
    for i in range(n):
        close = Decimal(f"{100.0 + steps[i]:.2f}")
        out.append(
            Bar(
                timestamp=start + timedelta(minutes=i),
                open=close,
                high=close + Decimal("0.20"),
                low=close - Decimal("0.20"),
                close=close,
                volume=1000 + i,
            )
        )
    return tuple(out)


def _chain(session: date, spot: float, received: datetime) -> tuple[OptionQuote, ...]:
    quotes: list[OptionQuote] = []
    centre = round(spot)
    for strike in range(centre - 6, centre + 7, 2):
        for side in (OptionType.CALL, OptionType.PUT):
            value = (
                max(spot - strike, 0.0)
                if side is OptionType.CALL
                else max(strike - spot, 0.0)
            )
            mid = Decimal(f"{value + 1.0:.2f}")
            quotes.append(
                OptionQuote(
                    contract=OptionContract(
                        contract_id=f"SPY-{strike}-{side.value}",
                        underlying_symbol="SPY",
                        expiration=session,
                        option_type=side,
                        strike=Decimal(str(strike)),
                    ),
                    received_at=received,
                    source="test",
                    bid=mid - Decimal("0.05"),
                    ask=mid + Decimal("0.05"),
                    volume=120 if side is OptionType.CALL else 150,
                    open_interest=1000 + strike,
                    gamma=0.02,
                    delta=0.5 if side is OptionType.CALL else -0.5,
                )
            )
    return tuple(quotes)


def _session_snapshots(
    session: date,
    *,
    seed: int,
    bars: int = FULL_SESSION_BARS,
    ticks: int = 40,
    every: int = 9,
) -> list[CanonicalMarketSnapshot]:
    """Snapshots across one session, each holding the bar path up to its tick."""
    path = _bars(session, bars, seed=seed)
    snapshots: list[CanonicalMarketSnapshot] = []
    for i in range(ticks):
        index = 20 + i * every
        if index >= len(path):
            break
        bar = path[index]
        spot = float(bar.close)
        snapshots.append(
            CanonicalMarketSnapshot(
                snapshot_id=f"snap-{session.isoformat()}-{i}",
                content_hash=f"sha256:{session.isoformat()}-{i}",
                timestamp=bar.timestamp,
                session_date=session,
                underlying_symbol="SPY",
                underlying_price=Decimal(f"{spot:.2f}"),
                session_status=SessionStatus.OPEN,
                bars_1m=path[: index + 1],
                option_chain=_chain(session, spot, bar.timestamp),
                minutes_to_close=len(path) - index,
                minutes_from_open=index,
            )
        )
    last = path[-1]
    snapshots.append(
        CanonicalMarketSnapshot(
            snapshot_id=f"snap-{session.isoformat()}-final",
            content_hash=f"sha256:{session.isoformat()}-final",
            timestamp=last.timestamp,
            session_date=session,
            underlying_symbol="SPY",
            underlying_price=last.close,
            session_status=SessionStatus.CLOSED,
            bars_1m=path,
            option_chain=_chain(session, float(last.close), last.timestamp),
            minutes_to_close=0,
            minutes_from_open=len(path),
        )
    )
    return snapshots


def _record(
    root: Path,
    sessions: int = 3,
    *,
    bars: int = FULL_SESSION_BARS,
    ticks: int = 40,
    every: int = 9,
) -> list[date]:
    """Write consecutive weekday recordings exactly as the market service does."""
    market = root / "market"
    market.mkdir(parents=True, exist_ok=True)
    written: list[date] = []
    day = date(2026, 1, 5)  # a Monday
    for i in range(sessions):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        snapshots = _session_snapshots(
            day, seed=100 + i, bars=bars, ticks=ticks, every=every
        )
        path = market / f"{day.isoformat()}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for seq, snap in enumerate(snapshots):
                handle.write(json.dumps(build_record(seq, snap), sort_keys=True))
                handle.write("\n")
        written.append(day)
        day += timedelta(days=1)
    return written


# --------------------------------------------------------------------------- #
# The gap itself                                                              #
# --------------------------------------------------------------------------- #
def test_the_dojo_scores_a_recorded_session(tmp_path: Path) -> None:
    """A state root full of recordings is all the Dojo should need.

    Before the native provider this returned `insufficient_data` with the note
    "no MarketExperienceProvider — wire 0DTE recorded feed", no matter how much
    tape SPY-DER had recorded.
    """
    _record(tmp_path, sessions=3)
    provider = NativeTapeProvider(tmp_path, interval_minutes=0)
    cfg = DojoConfig(min_sessions=3, min_ticks=100)

    result = run_recorded_phase(cfg, provider, authorities=default_authorities())

    assert result["status"] == "ok", result
    assert result["n_sessions"] == 3
    assert result["n_snapshots"] >= 100
    assert result["n_outcomes"] > 0
    # Champion and baseline both scored against real settlement.
    assert set(result["authorities"]) >= {"champion", "baseline"}
    assert result["evaluation"]["n_matched"] > 0


def test_sessions_are_discovered_from_the_market_directory(tmp_path: Path) -> None:
    written = _record(tmp_path, sessions=3)
    assert NativeTapeProvider(tmp_path).sessions() == written


def test_an_empty_state_root_yields_no_sessions(tmp_path: Path) -> None:
    assert NativeTapeProvider(tmp_path).sessions() == []


# --------------------------------------------------------------------------- #
# Packets carry the production candidate surface                              #
# --------------------------------------------------------------------------- #
def test_packets_carry_the_production_candidate_surface(tmp_path: Path) -> None:
    """The Dojo must score the candidates that ship, priced by the same economics."""
    session = _record(tmp_path, sessions=1)[0]
    packets = list(NativeTapeProvider(tmp_path).snapshots(session))

    assert packets
    assert all(p.candidates for p in packets)
    every = [c for p in packets for c in p.candidates]
    assert all(c.mid_price is not None for c in every)
    assert all(0.0 <= c.fill_probability <= 1.0 for c in every)
    assert all(c.maximum_loss >= 0 for c in every)


def test_an_unpriced_tape_says_so_instead_of_faking_a_ranking(tmp_path: Path) -> None:
    """The gap that would otherwise make the Dojo score the alphabet.

    `calculate_candidate_economics` only yields an expected value when a
    candidate-value forecast supplies `expected_net_pnl`. With none, the
    deterministic agent sorts on a None utility and falls through to candidate
    id. Emitting a 1..N `v3_rank` anyway would hand that arbitrary order to
    every downstream tiebreak dressed as a ranking.
    """
    session = _record(tmp_path, sessions=1)[0]
    provider = NativeTapeProvider(tmp_path)
    packets = list(provider.snapshots(session))

    assert all(c.utility is None for p in packets for c in p.candidates)
    assert all(c.v3_rank is None for p in packets for c in p.candidates)
    assert any("tape_unpriced" in w for w in provider.warnings())


def test_the_unpriced_count_does_not_inflate_when_phases_reread(tmp_path: Path) -> None:
    """The Dojo walks the tape once per phase; a counter would treble the total."""
    session = _record(tmp_path, sessions=1)[0]
    provider = NativeTapeProvider(tmp_path, interval_minutes=0)
    ticks = len(list(provider.snapshots(session)))
    for _ in range(2):
        list(provider.snapshots(session))

    warning = next(w for w in provider.warnings() if "tape_unpriced" in w)
    assert f"{ticks} tick(s)" in warning


def test_data_quality_comes_from_the_recording(tmp_path: Path) -> None:
    """A penalised snapshot must not arrive at the Dojo looking pristine."""
    market = tmp_path / "market"
    market.mkdir(parents=True)
    session = date(2026, 1, 5)
    snapshots = _session_snapshots(session, seed=7, ticks=3)
    penalised = [
        CanonicalMarketSnapshot(
            **{
                **{
                    f.name: getattr(s, f.name)
                    for f in s.__dataclass_fields__.values()  # type: ignore[attr-defined]
                },
                "data_quality": DataQuality(is_healthy=False, penalty=0.25),
            }
        )
        for s in snapshots
    ]
    with (market / f"{session.isoformat()}.jsonl").open("w", encoding="utf-8") as fh:
        for seq, snap in enumerate(penalised):
            fh.write(json.dumps(build_record(seq, snap), sort_keys=True) + "\n")

    packets = list(NativeTapeProvider(tmp_path, interval_minutes=0).snapshots(session))
    assert packets
    assert all(abs(p.data_quality - 0.75) < 1e-9 for p in packets)


# --------------------------------------------------------------------------- #
# Outcomes are settlement arithmetic, not a model                             #
# --------------------------------------------------------------------------- #
def test_settled_pnl_is_the_terminal_payoff_at_the_close(tmp_path: Path) -> None:
    """Check one long call against hand arithmetic, not against the same function."""
    session = _record(tmp_path, sessions=1)[0]
    provider = NativeTapeProvider(tmp_path, interval_minutes=0)
    packets = list(provider.snapshots(session))

    snapshots, _ = __import__(
        "spy_der.training.observations", fromlist=["load_session_snapshots"]
    ).load_session_snapshots(tmp_path / "market" / f"{session.isoformat()}.jsonl")
    settle = snapshots[-1].bars_1m[-1].close

    checked = 0
    for packet in packets:
        outcome = provider.outcome(packet.snapshot_id)
        assert outcome is not None
        by_candidate = outcome.labels["realized_pnl_by_candidate"]
        snapshot = next(s for s in snapshots if s.snapshot_id == packet.snapshot_id)
        for candidate in generate_candidate_universe(snapshot).candidates:
            if candidate.family != "long_call":
                continue
            leg = candidate.legs[0]
            # A long call settles at intrinsic; the debit paid is a negative credit.
            expected = candidate.entry_credit + intrinsic(
                OptionType.CALL, leg.strike, settle
            )
            assert Decimal(by_candidate[candidate.candidate_id]) == expected
            checked += 1
    assert checked, "no long_call candidate was available to verify"


def test_every_packet_candidate_has_a_settled_pnl(tmp_path: Path) -> None:
    """Selection regret needs the whole map — a partial one understates the best."""
    session = _record(tmp_path, sessions=1)[0]
    provider = NativeTapeProvider(tmp_path, interval_minutes=0)

    for packet in provider.snapshots(session):
        outcome = provider.outcome(packet.snapshot_id)
        assert outcome is not None
        settled = set(outcome.labels["realized_pnl_by_candidate"])
        assert {c.candidate_id for c in packet.candidates} == settled


def test_realized_direction_matches_the_sessions_actual_move(tmp_path: Path) -> None:
    session = _record(tmp_path, sessions=1)[0]
    provider = NativeTapeProvider(tmp_path, interval_minutes=0)
    packets = list(provider.snapshots(session))
    snapshots, _ = __import__(
        "spy_der.training.observations", fromlist=["load_session_snapshots"]
    ).load_session_snapshots(tmp_path / "market" / f"{session.isoformat()}.jsonl")
    settle = snapshots[-1].bars_1m[-1].close

    for packet in packets:
        outcome = provider.outcome(packet.snapshot_id)
        assert outcome is not None
        move = (settle - packet.underlying_price) / packet.underlying_price
        expected = (
            "neutral"
            if abs(float(move)) < 0.001
            else ("bullish" if move > 0 else "bearish")
        )
        assert outcome.labels["true_direction"] == expected


# --------------------------------------------------------------------------- #
# Absent stays absent                                                         #
# --------------------------------------------------------------------------- #
def test_an_unfinished_session_yields_packets_but_no_outcomes(tmp_path: Path) -> None:
    """Terminal payoff at noon prices every position as though it had expired.

    A recorder that died at lunchtime leaves a final bar that is a midday quote,
    not a settlement. Scoring against it would manufacture results for a day
    that never finished.
    """
    _record(tmp_path, sessions=1, bars=120, ticks=10, every=4)  # stops ~11:30 ET
    provider = NativeTapeProvider(tmp_path, interval_minutes=0)
    session = provider.sessions()[0]

    packets = list(provider.snapshots(session))
    assert packets, "an unfinished session should still provide market state"
    assert all(provider.outcome(p.snapshot_id) is None for p in packets)
    assert any(
        "tape_unsettled" in w and session.isoformat() in w for w in provider.warnings()
    )


def test_a_complete_session_reports_nothing_skipped(tmp_path: Path) -> None:
    session = _record(tmp_path, sessions=1)[0]
    provider = NativeTapeProvider(tmp_path, interval_minutes=0)
    list(provider.snapshots(session))
    assert not [w for w in provider.warnings() if "tape_unsettled" in w]


def test_a_snapshot_without_a_chain_produces_no_packet(tmp_path: Path) -> None:
    """A candidate-free packet can only ever abstain, diluting every rate."""
    market = tmp_path / "market"
    market.mkdir(parents=True)
    session = date(2026, 1, 5)
    stripped = [
        CanonicalMarketSnapshot(
            **{
                **{
                    f.name: getattr(s, f.name)
                    for f in s.__dataclass_fields__.values()  # type: ignore[attr-defined]
                },
                "option_chain": (),
            }
        )
        for s in _session_snapshots(session, seed=3, ticks=4)
    ]
    with (market / f"{session.isoformat()}.jsonl").open("w", encoding="utf-8") as fh:
        for seq, snap in enumerate(stripped):
            fh.write(json.dumps(build_record(seq, snap), sort_keys=True) + "\n")

    assert list(NativeTapeProvider(tmp_path).snapshots(session)) == []


# --------------------------------------------------------------------------- #
# Lookahead guard                                                             #
# --------------------------------------------------------------------------- #
def test_outcomes_are_never_reachable_from_the_packet(tmp_path: Path) -> None:
    """`MarketPacket.forecast` doubles as a label carrier — keep results out of it.

    `outcome_from_market_labels` reads realized P&L out of `forecast`, so
    embedding outcomes there would make packets self-contained at the cost of
    handing any authority that reads `market.forecast` the answer.
    """
    session = _record(tmp_path, sessions=1)[0]
    for packet in NativeTapeProvider(tmp_path).snapshots(session):
        assert packet.forecast == {}
        assert "pnl" not in json.dumps(packet.to_dict())


# --------------------------------------------------------------------------- #
# Sampling                                                                    #
# --------------------------------------------------------------------------- #
def test_sampling_is_by_wall_clock_not_by_record_index(tmp_path: Path) -> None:
    """Tape size must not depend on how often the recorder happened to tick."""
    coarse = tmp_path / "coarse"
    fine = tmp_path / "fine"
    # Same session span, one recorded 4x as often as the other.
    _record(coarse, sessions=1, ticks=20, every=16)
    _record(fine, sessions=1, ticks=80, every=4)

    session = date(2026, 1, 5)
    n_coarse = len(list(NativeTapeProvider(coarse, interval_minutes=16).snapshots(session)))
    n_fine = len(list(NativeTapeProvider(fine, interval_minutes=16).snapshots(session)))

    assert abs(n_coarse - n_fine) <= 1, (n_coarse, n_fine)


def test_zero_interval_keeps_every_snapshot(tmp_path: Path) -> None:
    session = _record(tmp_path, sessions=1, ticks=10, every=4)[0]
    every = len(list(NativeTapeProvider(tmp_path, interval_minutes=0).snapshots(session)))
    spaced = len(list(NativeTapeProvider(tmp_path, interval_minutes=30).snapshots(session)))
    assert every > spaced


# --------------------------------------------------------------------------- #
# Protocol robustness                                                         #
# --------------------------------------------------------------------------- #
def test_outcome_resolves_before_snapshots_are_walked(tmp_path: Path) -> None:
    """The protocol does not promise call order; a cold `outcome()` must still work."""
    session = _record(tmp_path, sessions=1)[0]
    warm = NativeTapeProvider(tmp_path, interval_minutes=0)
    known = next(iter(warm.snapshots(session))).snapshot_id

    cold = NativeTapeProvider(tmp_path, interval_minutes=0)
    assert cold.outcome(known) is not None


def test_an_unknown_snapshot_id_has_no_outcome(tmp_path: Path) -> None:
    _record(tmp_path, sessions=1)
    assert NativeTapeProvider(tmp_path).outcome("snap-does-not-exist") is None


def test_a_corrupt_recording_costs_its_session_not_the_run(tmp_path: Path) -> None:
    sessions = _record(tmp_path, sessions=2)
    bad = tmp_path / "market" / f"{sessions[0].isoformat()}.jsonl"
    bad.write_text("this is not a record\n", encoding="utf-8")

    provider = NativeTapeProvider(tmp_path, interval_minutes=0)
    assert list(provider.snapshots(sessions[0])) == []
    assert list(provider.snapshots(sessions[1])), "the good session must survive"
