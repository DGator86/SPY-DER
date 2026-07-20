"""Shared read helpers for PolicyInputPacket (no mutation)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from spy_der.contracts.policies import PolicyInputPacket

__all__ = [
    "candidate_ids",
    "candidate_max_loss",
    "envelope_max_risk",
    "hard_veto_codes",
    "legacy_permissions_ok",
    "legacy_size_cap",
    "ranked_candidate_ids",
    "top_value_candidate_id",
]


def hard_veto_codes(packet: PolicyInputPacket) -> tuple[str, ...]:
    legacy = packet.legacy_view
    if legacy is None:
        return ()
    vetoes = getattr(legacy, "hard_vetoes", ()) or ()
    codes: list[str] = []
    for veto in vetoes:
        code = getattr(veto, "code", veto)
        codes.append(getattr(code, "value", str(code)))
    return tuple(codes)


def legacy_permissions_ok(packet: PolicyInputPacket) -> bool:
    legacy = packet.legacy_view
    if legacy is None:
        return False
    perms = getattr(legacy, "permissions", None)
    if perms is not None:
        return bool(
            getattr(perms, "options_allowed", False)
            and getattr(perms, "new_positions_allowed", False)
        )
    # Rich LegacyDecisionView: tradeable iff no vetoes + permitted families.
    is_tradeable = getattr(legacy, "is_tradeable", None)
    if callable(is_tradeable):
        return bool(is_tradeable)
    if hasattr(legacy, "is_tradeable"):
        return bool(legacy.is_tradeable)
    return not hard_veto_codes(packet)


def legacy_size_cap(packet: PolicyInputPacket) -> float:
    legacy = packet.legacy_view
    if legacy is None:
        return 0.0
    return float(getattr(legacy, "size_cap", 0.0) or 0.0)


def candidate_ids(packet: PolicyInputPacket) -> tuple[str, ...]:
    universe = packet.candidate_universe
    if universe is None:
        return ()
    cands = getattr(universe, "candidates", ()) or ()
    out: list[str] = []
    for cand in cands:
        cid = getattr(cand, "candidate_id", "")
        if cid:
            out.append(str(cid))
    return tuple(out)


def candidate_max_loss(packet: PolicyInputPacket, candidate_id: str) -> Decimal | None:
    universe = packet.candidate_universe
    if universe is None:
        return None
    for cand in getattr(universe, "candidates", ()) or ():
        if getattr(cand, "candidate_id", None) == candidate_id:
            loss = getattr(cand, "max_loss", None)
            if loss is None:
                loss = getattr(cand, "maximum_loss", None)
            return Decimal(str(loss)) if loss is not None else None
    return None


def envelope_max_risk(packet: PolicyInputPacket) -> Decimal | None:
    env = packet.risk_envelope
    if env is None:
        return None
    value = getattr(env, "max_defined_risk_per_trade", None)
    if value is None:
        value = getattr(env, "max_risk_dollars", None)
    return Decimal(str(value)) if value is not None else None


def ranked_candidate_ids(packet: PolicyInputPacket) -> tuple[str, ...]:
    ranking = packet.ranking
    if ranking is None:
        return ()
    ordered = getattr(ranking, "ordered_candidate_ids", None)
    if ordered is not None:
        return tuple(str(x) for x in ordered)
    # SnapshotRanking compatibility via top_candidate_id
    top = getattr(ranking, "top_candidate_id", None)
    return (str(top),) if top else ()


def top_value_candidate_id(packet: PolicyInputPacket) -> str | None:
    forecasts = packet.value_forecasts or ()
    best_id: str | None = None
    best_util = float("-inf")
    for fc in forecasts:
        cid = getattr(fc, "candidate_id", None)
        util = getattr(fc, "utility", None)
        if cid is None or util is None:
            continue
        if float(util) > best_util:
            best_util = float(util)
            best_id = str(cid)
    return best_id


def forecast_field(packet: PolicyInputPacket, name: str) -> Any:
    forecast = packet.market_forecast
    if forecast is None:
        return None
    return getattr(forecast, name, None)
