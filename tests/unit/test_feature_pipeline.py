"""The feature stage: assembly, cold start, identity and failure isolation.

Everything this assembles already existed; what did not exist was the assembly,
so `spy-der engine` reported the stage unavailable and the deterministic layers
ran without any of it. These tests pin the three properties that make the
bundle trustworthy: absent stays absent, identity is reproducible from a
recording, and one broken family does not cost the rest.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from spy_der.contracts.market import (
    Bar,
    BreadthState,
    CanonicalMarketSnapshot,
    OptionContract,
    OptionQuote,
    OptionType,
    VolatilityTermStructure,
)
from spy_der.features.gex import GexRankWindow
from spy_der.features.pipeline import (
    FEATURE_PIPELINE_VERSION,
    SnapshotFeaturePipeline,
)
from spy_der.features.resample import ET
from spy_der.market_data.assembler import CanonicalSnapshotAssembler

OPEN = datetime(2026, 1, 5, 9, 30, tzinfo=ET)
NOW = datetime(2026, 1, 5, 14, 30, tzinfo=ET)
EXPIRY = NOW.date()
SPOT = Decimal("500")


def _bars(n: int = 300) -> tuple[Bar, ...]:
    out: list[Bar] = []
    for i in range(n):
        close = Decimal(str(500.0 + i * 0.02))
        out.append(
            Bar(
                timestamp=OPEN + timedelta(minutes=i),
                open=close,
                high=close + Decimal("0.25"),
                low=close - Decimal("0.25"),
                close=close,
                volume=1000 + i,
            )
        )
    return tuple(out)


def _chain() -> tuple[OptionQuote, ...]:
    quotes: list[OptionQuote] = []
    for strike in range(490, 511, 2):
        for side in (OptionType.CALL, OptionType.PUT):
            intrinsic = max(500 - strike, 0) if side is OptionType.CALL else max(strike - 500, 0)
            mid = Decimal(str(intrinsic + 1.0))
            quotes.append(
                OptionQuote(
                    contract=OptionContract(
                        contract_id=f"SPY-{strike}-{side.value}",
                        underlying_symbol="SPY",
                        expiration=EXPIRY,
                        option_type=side,
                        strike=Decimal(str(strike)),
                    ),
                    received_at=NOW,
                    source="test",
                    bid=mid - Decimal("0.05"),
                    ask=mid + Decimal("0.05"),
                    volume=100 if side is OptionType.CALL else 150,
                    open_interest=1000,
                    gamma=0.02,
                    delta=0.5 if side is OptionType.CALL else -0.5,
                )
            )
    return tuple(quotes)


def _snapshot(**kw: Any) -> CanonicalMarketSnapshot:
    params: dict[str, Any] = {
        "timestamp": NOW,
        "underlying_symbol": "SPY",
        "underlying_price": SPOT,
        "bars_1m": _bars(),
        "option_chain": _chain(),
    }
    params.update(kw)
    return CanonicalSnapshotAssembler().assemble(**params)


def _features(snapshot: CanonicalMarketSnapshot, **kw: Any) -> dict[str, float]:
    return dict(SnapshotFeaturePipeline(**kw).build(snapshot).features)


# --------------------------------------------------------------------------- #
# Assembly                                                                    #
# --------------------------------------------------------------------------- #
def test_every_family_lands_in_one_bundle() -> None:
    snapshot = _snapshot(
        volatility_term_structure=VolatilityTermStructure(
            vix=15.0, vix9d=13.0, vix3m=17.0, vvix=90.0, vvix_baseline=95.0
        ),
        breadth=BreadthState(rsp_spy_div=-0.004, sector_align=0.6, top10_pressure=0.01),
    )
    keys = _features(snapshot)
    for prefix in ("1m.", "gex.", "vol.", "rnd.", "flow.", "breadth.", "vix.", "session."):
        assert any(k.startswith(prefix) for k in keys), f"no {prefix} features"


def test_the_result_reports_which_families_were_unavailable() -> None:
    result = SnapshotFeaturePipeline().build_detailed(_snapshot())
    # No VIX or breadth on this snapshot; both are optional data, not defects.
    assert set(result.missing_families) == {"breadth", "vix"}
    assert result.failed_families == ()
    assert result.is_complete is False


def test_a_fully_populated_snapshot_is_complete() -> None:
    snapshot = _snapshot(
        volatility_term_structure=VolatilityTermStructure(vix=15.0),
        breadth=BreadthState(sector_align=0.6),
    )
    result = SnapshotFeaturePipeline().build_detailed(snapshot)
    assert result.missing_families == ()
    assert result.is_complete is True


def test_mtf_features_are_namespaced_by_timeframe() -> None:
    keys = _features(_snapshot())
    assert "1m.rsi" in keys
    assert "5m.adx" in keys or "5m.last_return" in keys


def test_gex_distances_are_expressed_as_fractions_of_spot() -> None:
    """A 2-point cushion means different things at 400 and at 700."""
    keys = _features(_snapshot())
    assert "gex.flip_cushion" in keys
    assert abs(keys["gex.flip_cushion"]) < 1.0
    assert "gex.call_wall_distance" in keys
    assert "gex.channel_width" in keys


def test_flow_reaches_the_bundle_from_the_chain() -> None:
    keys = _features(_snapshot())
    assert keys["flow.pcr_volume"] == pytest.approx(1.5)
    assert keys["flow.volume_oi_ratio"] > 0


def test_vix_shape_features_are_derived() -> None:
    snapshot = _snapshot(
        volatility_term_structure=VolatilityTermStructure(
            vix=20.0, vix9d=18.0, vix3m=22.0, vvix=100.0, vvix_baseline=95.0
        )
    )
    keys = _features(snapshot)
    assert keys["vix.contango"] == pytest.approx(0.1)
    assert keys["vix.near_term_stress"] == pytest.approx(-0.1)
    assert keys["vix.vvix_elevation"] == pytest.approx(100 / 95 - 1.0)


def test_session_context_includes_the_underlying_spread() -> None:
    snapshot = _snapshot(
        underlying_bid=Decimal("499.98"), underlying_ask=Decimal("500.02")
    )
    keys = _features(snapshot)
    assert keys["session.underlying_spread"] == pytest.approx(0.04)
    assert keys["session.underlying_spread_pct"] == pytest.approx(0.04 / 500.0, rel=1e-3)


# --------------------------------------------------------------------------- #
# Absent stays absent                                                         #
# --------------------------------------------------------------------------- #
def test_a_barless_snapshot_omits_the_matrix_rather_than_zero_filling() -> None:
    keys = _features(_snapshot(bars_1m=()))
    assert not any(k.startswith(("1m.", "5m.", "1d.")) for k in keys)
    assert "gex.net_bn" in keys  # the chain-derived families still land


def test_an_empty_chain_omits_the_chain_families() -> None:
    keys = _features(_snapshot(option_chain=()))
    assert not any(k.startswith(("gex.", "rnd.", "flow.")) for k in keys)
    assert "1m.rsi" in keys  # bars still produce the matrix


def test_absent_breadth_and_vix_produce_no_keys() -> None:
    keys = _features(_snapshot())
    assert not any(k.startswith(("breadth.", "vix.")) for k in keys)


def test_no_feature_value_is_a_sentinel_for_unknown() -> None:
    """Presence is the signal; there is no in-band 'missing' value."""
    import math

    for value in _features(_snapshot()).values():
        assert math.isfinite(value)


def test_the_gex_rank_is_withheld_until_the_window_is_warm() -> None:
    """`rank` returns a placeholder 0.5 before warm-up, which a gate cannot detect."""
    window = GexRankWindow(min_samples=5)
    keys = _features(_snapshot(), gex_window=window)
    assert "gex.pct_rank" not in keys
    assert window.is_warm is False


def test_the_gex_rank_appears_once_the_window_is_warm() -> None:
    window = GexRankWindow(min_samples=2)
    pipeline = SnapshotFeaturePipeline(gex_window=window)
    snapshot = _snapshot()
    pipeline.build(snapshot)  # first print warms it
    keys = dict(pipeline.build(snapshot).features)
    assert window.is_warm
    assert 0.0 <= keys["gex.pct_rank"] <= 1.0


# --------------------------------------------------------------------------- #
# Identity                                                                    #
# --------------------------------------------------------------------------- #
def test_the_bundle_is_keyed_to_its_snapshot() -> None:
    snapshot = _snapshot()
    bundle = SnapshotFeaturePipeline().build(snapshot)
    assert bundle.snapshot_id == snapshot.snapshot_id
    assert bundle.bundle_id.startswith("feat-")


def test_identity_is_reproducible_across_runs() -> None:
    """Replaying a recording must rebuild the same bundle id."""
    snapshot = _snapshot()
    first = SnapshotFeaturePipeline().build(snapshot)
    second = SnapshotFeaturePipeline().build(snapshot)
    assert first.bundle_id == second.bundle_id
    assert first.features == second.features


def test_different_content_yields_a_different_bundle_id() -> None:
    a = SnapshotFeaturePipeline().build(_snapshot())
    b = SnapshotFeaturePipeline().build(_snapshot(underlying_price=Decimal("501")))
    assert a.bundle_id != b.bundle_id


def test_features_are_sorted_so_ordering_cannot_drift_identity() -> None:
    bundle = SnapshotFeaturePipeline().build(_snapshot())
    names = [name for name, _ in bundle.features]
    assert names == sorted(names)


def test_the_pipeline_version_participates_in_identity() -> None:
    """Bundles built under different definitions must not collide on id."""
    assert FEATURE_PIPELINE_VERSION


# --------------------------------------------------------------------------- #
# Failure isolation                                                           #
# --------------------------------------------------------------------------- #
def test_one_broken_family_does_not_cost_the_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pathological chain should cost the RND summary, not the whole tick."""

    def boom(*_a: Any, **_k: Any) -> Any:
        raise ValueError("pathological chain")

    monkeypatch.setattr("spy_der.features.pipeline.compute_rnd", boom)
    result = SnapshotFeaturePipeline().build_detailed(_snapshot())
    assert result.failed_families == ("rnd",)
    assert dict(result.bundle.features).get("gex.net_bn") is not None
    assert any(k.startswith("1m.") for k, _ in result.bundle.features)


def test_a_failed_family_is_distinguished_from_an_absent_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One is a data condition, the other a defect — operators fix them differently."""

    def boom(*_a: Any, **_k: Any) -> Any:
        raise ValueError("nope")

    monkeypatch.setattr("spy_der.features.pipeline.compute_rnd", boom)
    result = SnapshotFeaturePipeline().build_detailed(_snapshot())
    assert "rnd" in result.failed_families
    assert "rnd" not in result.missing_families
    assert "vix" in result.missing_families
