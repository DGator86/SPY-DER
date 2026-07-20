from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from spy_der.contracts.market import Bar
from spy_der.features import RobustStandardizer, compute_mtf

ET = ZoneInfo("America/New_York")
START = datetime(2026, 1, 5, 9, 30, tzinfo=ET)


def _bars(closes: list[float]) -> tuple[Bar, ...]:
    out: list[Bar] = []
    for i, close in enumerate(closes):
        ts = START + timedelta(minutes=i)
        c = Decimal(str(close))
        out.append(Bar(ts, c, c, c, c, 1000))
    return tuple(out)


# -------------------------------------------------------------------- MTF ----
def test_mtf_cold_start_reports_none() -> None:
    features = compute_mtf(_bars([500.0, 500.5]))
    by_tf = {f.timeframe_minutes: f for f in features}
    assert by_tf[1].rsi is None  # not enough history
    assert by_tf[1].last_return is not None


def test_mtf_indicators_on_trend() -> None:
    closes = [500.0 + i * 0.5 for i in range(60)]  # steady uptrend
    features = compute_mtf(_bars(closes))
    by_tf = {f.timeframe_minutes: f for f in features}
    assert set(by_tf) == {1, 5, 15}
    one = by_tf[1]
    assert one.last_return is not None and one.last_return > 0
    assert one.ema_slope is not None and one.ema_slope > 0
    assert one.rsi is not None and one.rsi > 70  # strong uptrend -> high RSI
    assert one.realized_vol is not None and one.realized_vol >= 0
    # 5m resample has ~1/5 the bars of the 1m series.
    assert by_tf[5].n_bars == 12


def test_mtf_resamples_fewer_bars() -> None:
    closes = [500.0 + i for i in range(30)]
    by_tf = {f.timeframe_minutes: f for f in compute_mtf(_bars(closes))}
    assert by_tf[1].n_bars == 30
    assert by_tf[5].n_bars == 6
    assert by_tf[15].n_bars == 2


# ---------------------------------------------------------- normalization ----
def test_standardizer_neutral_until_warm() -> None:
    std = RobustStandardizer(min_samples=10)
    for i in range(9):
        assert std.score("gex", float(i)) is None
    assert not std.is_warm("gex")
    assert std.score("gex", 9.0) is None  # 10th observation still scores against 9
    assert std.is_warm("gex")


def test_standardizer_scores_outlier_high() -> None:
    std = RobustStandardizer(min_samples=5)
    for v in [10.0, 11.0, 9.0, 10.5, 9.5, 10.2, 9.8]:
        std.score("x", v)
    z = std.score("x", 20.0)  # far above the ~10 cluster
    assert z is not None and z > 3.0


def test_standardizer_persists_across_restart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = str(tmp_path / "norm.json")
    first = RobustStandardizer(min_samples=3, path=path)
    for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
        first.score("k", v)
    second = RobustStandardizer(min_samples=3, path=path)
    assert second.is_warm("k")
    assert second.score("k", 3.0) is not None
