"""Build the market and forecast context the entry agent decides against.

The entry packet used to carry candidate summaries and nothing else — a list of
geometries with a utility score, with no view of the dealer positioning, the
volatility surface, the trend structure or the walls those geometries sit
between. An agent given only that can re-rank what it was handed but cannot
disagree with the ranking *on evidence*, which is the only thing it adds over
the deterministic ordering it already received.

Everything here is derived from a canonical snapshot and its feature bundle, so
it inherits their rules unchanged:

* **Absent stays absent.** A field with no reading is omitted, never zero-filled
  — the agent must be able to see that GEX is unknown rather than flat.
* **Processed outputs only.** Numbers the deterministic stages already computed.
  No credentials, no tools, no order handles, no raw vendor payloads (spec §41).
* **Data, not instructions.** The prompt layer states this explicitly; nothing
  here should ever carry free text sourced from outside the system.

``CORE_TECHNICALS`` is a deliberate subset of the ~30-field multi-timeframe
matrix. The full matrix across seven timeframes is a couple of hundred numbers,
and this is a per-tick LLM call under a spend throttle: the subset is the
indicators a discretionary trader would actually name when explaining a 0DTE
entry. Callers that want everything can pass ``technical_fields=None``.
"""

from __future__ import annotations

from collections.abc import Container
from decimal import Decimal, InvalidOperation

from spy_der.contracts.agents import ForecastContext, MarketContext
from spy_der.contracts.forecasts import MarketForecastBundle
from spy_der.contracts.market import CanonicalMarketSnapshot
from spy_der.contracts.models import FeatureBundle

__all__ = [
    "CORE_TECHNICALS",
    "FORECAST_HORIZON_FIELDS",
    "build_forecast_context",
    "build_market_context",
]

#: Per-timeframe indicators included by default.
CORE_TECHNICALS: frozenset[str] = frozenset(
    {
        "rsi",
        "adx",
        "di_spread",
        "ema_slope",
        "trend_cleanliness",
        "realized_vol",
        "rv_expansion",
        "dist_to_vwap",
        "vwap_slope",
        "range_position",
        "bb_squeeze",
        "bb_position",
        "keltner_position",
        "donchian_position",
        "donchian_breakout_up",
        "donchian_breakout_down",
        "cvd_persistence",
    }
)

#: Forecast fields worth showing the agent: probabilities, expected moves and
#: the quantile spread. The model-machinery fields (ids, versions) are carried
#: separately so the horizon map stays purely numeric.
FORECAST_HORIZON_FIELDS: tuple[str, ...] = (
    "p_up_5m",
    "p_up_15m",
    "p_up_30m",
    "p_up_60m",
    "p_up_close",
    "expected_return_15m",
    "expected_return_30m",
    "expected_return_60m",
    "expected_return_close",
    "return_q10_30m",
    "return_q50_30m",
    "return_q90_30m",
    "return_q10_close",
    "return_q50_close",
    "return_q90_close",
    "expected_realized_move_30m",
    "expected_realized_move_close",
    "p_range_survive_15m",
    "p_range_survive_30m",
    "p_range_survive_60m",
    "p_range_survive_close",
)


def _decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(f"{value:.4f}")
    except (InvalidOperation, ValueError):
        return None


def _technicals(
    features: dict[str, float], fields: Container[str] | None
) -> tuple[tuple[str, float], ...]:
    """Timeframe-prefixed indicator keys, filtered to ``fields`` when given."""
    out: list[tuple[str, float]] = []
    for key, value in features.items():
        if "." not in key:
            continue
        prefix, _, name = key.partition(".")
        # Timeframe labels end in a unit; the feature families do not.
        if not prefix[:1].isdigit():
            continue
        if fields is None or name in fields:
            out.append((key, value))
    return tuple(sorted(out))


def build_market_context(
    snapshot: CanonicalMarketSnapshot,
    *,
    features: FeatureBundle | None = None,
    technical_fields: Container[str] | None = CORE_TECHNICALS,
) -> MarketContext:
    """Assemble the decision-relevant market state.

    ``features`` is optional: without it the context still carries the
    underlying, the session position and the data-quality flags, which is
    strictly better than the nothing the packet carried before. With it, the
    dealer, volatility, flow, breadth and technical picture come too.
    """
    values: dict[str, float] = dict(features.features) if features else {}

    def get(name: str) -> float | None:
        return values.get(name)

    gamma_sign = get("gex.gamma_sign")
    return MarketContext(
        underlying_price=snapshot.underlying_price,
        underlying_bid=snapshot.underlying_bid,
        underlying_ask=snapshot.underlying_ask,
        minutes_from_open=snapshot.minutes_from_open,
        minutes_to_close=snapshot.minutes_to_close,
        net_gex_bn=get("gex.net_bn"),
        gamma_sign=int(gamma_sign) if gamma_sign is not None else None,
        gamma_flip=_decimal(get("gex.gamma_flip")),
        call_wall=_decimal(get("gex.call_wall")),
        put_wall=_decimal(get("gex.put_wall")),
        flip_cushion=get("gex.flip_cushion"),
        call_wall_distance=get("gex.call_wall_distance"),
        put_wall_distance=get("gex.put_wall_distance"),
        gex_pct_rank=get("gex.pct_rank"),
        atm_straddle=_decimal(get("vol.atm_straddle")),
        expected_move=_decimal(get("vol.expected_move")),
        expected_move_pct=get("vol.expected_move_pct"),
        expected_move_consumed=get("vol.expected_move_consumed"),
        vix=get("vix.vix"),
        vix9d=get("vix.vix9d"),
        vix3m=get("vix.vix3m"),
        vvix=get("vix.vvix"),
        vix_contango=get("vix.contango"),
        rnd_skew=get("rnd.skew"),
        rnd_prob_below_spot=get("rnd.prob_below_spot"),
        pcr_volume=get("flow.pcr_volume"),
        volume_oi_ratio=get("flow.volume_oi_ratio"),
        rsp_spy_div=get("breadth.rsp_spy_div"),
        sector_align=get("breadth.sector_align"),
        top10_pressure=get("breadth.top10_pressure"),
        technicals=_technicals(values, technical_fields),
        data_quality_flags=snapshot.data_quality.flags,
    )


def build_forecast_context(
    forecast: MarketForecastBundle | None,
) -> ForecastContext | None:
    """Flatten the forecast to its conclusions; ``None`` when none ran.

    Returning ``None`` rather than an empty context matters: the forecast stage
    is fail-closed and frequently unavailable, and the agent must be able to
    tell "no forecast" from "a forecast with nothing in it".
    """
    if forecast is None:
        return None
    horizons = tuple(
        (name, float(value))
        for name in FORECAST_HORIZON_FIELDS
        if (value := getattr(forecast, name, None)) is not None
    )
    if not horizons:
        return None
    return ForecastContext(
        forecast_id=forecast.forecast_id,
        model_group_id=forecast.model_group_id or forecast.model_version,
        horizons=horizons,
    )
