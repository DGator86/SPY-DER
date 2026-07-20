"""Fill-attempt recording and provenance validation (spec §34)."""

from __future__ import annotations

from decimal import Decimal

from spy_der.contracts.economics import FILL_RECORD_VERSION, FillRecord

__all__ = [
    "ALLOWED_MODES",
    "ALLOWED_SOURCES",
    "FILL_RECORD_VERSION",
    "FillRecord",
    "enrich_fill_fractions",
    "fill_fraction",
    "validate_fill_record",
]

ALLOWED_SOURCES = frozenset(
    {
        "paper",
        "manual_paper",
        "broker_actual",
        "user_confirmed",
        "hypothetical",
        "rejected",
        "cancelled",
        "expired_unfilled",
        "advisory",
    }
)

ALLOWED_MODES = frozenset(
    {"research", "shadow", "advisory", "candidate", "champion", "paper"}
)


def fill_fraction(
    mid_credit: Decimal | float,
    natural_credit: Decimal | float,
    fill_credit: Decimal | float,
    *,
    epsilon: float = 1e-9,
) -> tuple[float, float]:
    """Concession from midpoint toward natural. Returns (raw, clipped in [0,1])."""
    mid = float(mid_credit)
    nat = float(natural_credit)
    fill = float(fill_credit)
    denom = mid - nat
    raw = 0.0 if abs(denom) < epsilon else (mid - fill) / denom
    clipped = float(min(max(raw, 0.0), 1.0))
    return float(raw), clipped


def validate_fill_record(rec: FillRecord) -> None:
    """Raise ValueError on provenance / timestamp / identity violations."""
    if not rec.fill_record_id:
        raise ValueError("fill_record_id required")
    if not rec.snapshot_id or not rec.candidate_id:
        raise ValueError("snapshot_id and candidate_id required")
    if rec.source not in ALLOWED_SOURCES:
        raise ValueError(f"unknown fill source: {rec.source}")
    if rec.mode not in ALLOWED_MODES:
        raise ValueError(f"unknown fill mode: {rec.mode}")
    diag = dict(rec.diagnostics)
    if rec.source == "broker_actual" and diag.get("simulated") == "true":
        raise ValueError("simulated fill cannot be source=broker_actual")
    if diag.get("midpoint_diagnostic") == "true" and rec.filled:
        raise ValueError("midpoint diagnostic cannot be labeled filled")
    if rec.source in ("hypothetical", "advisory") and rec.filled:
        raise ValueError(f"source={rec.source} must have filled=False")
    if rec.decision_ts and rec.submitted_ts and rec.decision_ts > rec.submitted_ts:
        raise ValueError("decision_ts must be <= submitted_ts")
    if rec.resolved_ts is not None and rec.submitted_ts and rec.resolved_ts < rec.submitted_ts:
        raise ValueError("resolved_ts must be >= submitted_ts")
    if rec.filled and rec.fill_credit is None:
        raise ValueError("filled records require fill_credit")
    if rec.partial_fill and rec.filled_quantity <= 0:
        raise ValueError("partial_fill requires filled_quantity > 0")


def enrich_fill_fractions(rec: FillRecord) -> FillRecord:
    """Attach raw/clipped fill fractions when a fill_credit is present."""
    if rec.fill_credit is None:
        return rec
    raw, clipped = fill_fraction(
        rec.mid_credit_at_submit,
        rec.natural_credit_at_submit,
        rec.fill_credit,
    )
    return FillRecord(
        fill_record_id=rec.fill_record_id,
        snapshot_id=rec.snapshot_id,
        candidate_id=rec.candidate_id,
        session_date=rec.session_date,
        decision_ts=rec.decision_ts,
        submitted_ts=rec.submitted_ts,
        resolved_ts=rec.resolved_ts,
        symbol=rec.symbol,
        family=rec.family,
        side=rec.side,
        n_legs=rec.n_legs,
        limit_credit=rec.limit_credit,
        mid_credit_at_submit=rec.mid_credit_at_submit,
        natural_credit_at_submit=rec.natural_credit_at_submit,
        relative_spread=rec.relative_spread,
        absolute_spread=rec.absolute_spread,
        option_price_scale=rec.option_price_scale,
        quote_age_seconds=rec.quote_age_seconds,
        minutes_to_close=rec.minutes_to_close,
        realized_volatility=rec.realized_volatility,
        implied_remaining_move=rec.implied_remaining_move,
        dominant_regime=rec.dominant_regime,
        data_quality=rec.data_quality,
        replacement_count=rec.replacement_count,
        replacement_prices=rec.replacement_prices,
        filled=rec.filled,
        partial_fill=rec.partial_fill,
        filled_quantity=rec.filled_quantity,
        requested_quantity=rec.requested_quantity,
        seconds_to_first_fill=rec.seconds_to_first_fill,
        seconds_to_complete_fill=rec.seconds_to_complete_fill,
        fill_credit=rec.fill_credit,
        fill_fraction=clipped,
        fill_fraction_raw=raw,
        fill_fraction_clipped=clipped,
        cancelled=rec.cancelled,
        expired_unfilled=rec.expired_unfilled,
        rejected=rec.rejected,
        source=rec.source,
        mode=rec.mode,
        version=rec.version,
        diagnostics=rec.diagnostics,
    )
