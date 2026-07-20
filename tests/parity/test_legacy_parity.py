"""Phase 4 Legacy-view parity fixture (master spec §23, §65).

A fixed long-gamma structural scenario is run through the Legacy analyzer and
must reproduce a frozen canonical `LegacyDecisionView`
(`baseline/expected_outputs/phase4/`) bit-for-bit, plus a frozen view id. This
locks the Legacy permission/veto/direction contract.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from spy_der.contracts import to_canonical_json
from spy_der.contracts.market import (
    Bar,
    FeedComponent,
    OptionContract,
    OptionQuote,
    OptionType,
)
from spy_der.contracts.structure import GexLevels, StructuralState
from spy_der.legacy import LegacyAnalyzer
from spy_der.market_data import CanonicalSnapshotAssembler, build_observation

_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = _ROOT / "baseline" / "expected_outputs" / "phase4" / "legacy_view.json"

ET = ZoneInfo("America/New_York")
TS = datetime(2026, 1, 5, 14, 30, tzinfo=ET)
_EXPECTED_VIEW_ID = "legacy-3597040c8310"


def _snapshot() -> object:
    quotes: list[OptionQuote] = []
    for strike in (495, 500, 505, 510, 515):
        for kind in (OptionType.CALL, OptionType.PUT):
            contract = OptionContract(
                contract_id=f"SPY-{strike}-{kind.value}",
                underlying_symbol="SPY",
                expiration=date(2026, 1, 5),
                option_type=kind,
                strike=Decimal(strike),
            )
            quotes.append(OptionQuote(contract=contract, received_at=TS, source="fixture"))
    obs = tuple(
        build_observation(c, "tradier", TS, 60.0, observed_at=TS)
        for c in (
            FeedComponent.SPOT,
            FeedComponent.BARS,
            FeedComponent.OPTION_CHAIN,
            FeedComponent.SETTLEMENT,
        )
    )
    bar = Bar(TS, Decimal("500"), Decimal("501"), Decimal("499"), Decimal("500"), 1000)
    return CanonicalSnapshotAssembler().assemble(
        timestamp=TS,
        underlying_symbol="SPY",
        underlying_price=Decimal("500"),
        bars_1m=(bar,),
        option_chain=tuple(quotes),
        feed_observations=obs,
    )


def _view() -> object:
    snapshot = _snapshot()
    gex = GexLevels(
        net_gex_bn=0.75,
        net_ratio=0.6,
        gamma_flip=Decimal("500"),
        call_wall=Decimal("510"),
        put_wall=Decimal("490"),
        gex_concentration=0.4,
        wall_concentration=0.3,
        n_contracts=10,
        n_strikes=5,
    )
    state = StructuralState(
        structural_state_id="struct-fixture-0001",
        snapshot_id=snapshot.snapshot_id,  # type: ignore[attr-defined]
        gex_oi=gex,
    )
    return LegacyAnalyzer().analyze(snapshot, state)  # type: ignore[arg-type]


def test_legacy_view_matches_golden() -> None:
    produced = json.loads(to_canonical_json(_view()))
    expected = json.loads(_EXPECTED.read_text())
    assert produced == expected


def test_legacy_view_id_is_frozen() -> None:
    assert _view().view_id == _EXPECTED_VIEW_ID  # type: ignore[attr-defined]
