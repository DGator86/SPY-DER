"""Rebuild `CanonicalMarketSnapshot` from its canonical JSON form.

`spy_der.contracts.serialization.to_canonical_json` is a one-way street:
dataclasses become dicts, `Decimal` and `datetime` become strings, enums become
their values. `spy_der.market_data.recording` writes snapshots through it and
`ReplayFeed` hands the dicts back, but nothing turned a dict back into the typed
snapshot — so replay could verify a recording's integrity without being able to
run anything on it. `spy-der engine` needs exactly that, and the promise in the
recording module's docstring ("reproduce identity without a network") is not
kept until it exists.

Parsing is fail-closed, matching the rest of the contracts layer: a missing
required field or an unparseable value raises rather than defaulting. Bad data
is flagged, never silently repaired (spec §13.4).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from spy_der.contracts.common import NORMALIZATION_VERSION, ContractError, ErrorCode
from spy_der.contracts.market import (
    Bar,
    BreadthState,
    CanonicalMarketSnapshot,
    CatalystState,
    ChainCoverage,
    DataQuality,
    FeedComponent,
    FeedObservation,
    FeedStatus,
    OptionContract,
    OptionQuote,
    OptionType,
    ProviderSelection,
    SessionStatus,
    VolatilityTermStructure,
)

__all__ = ["SnapshotParseError", "snapshot_from_dict"]


class SnapshotParseError(ContractError):
    """A recorded snapshot could not be rebuilt into its typed form."""


def _fail(field: str, detail: str) -> SnapshotParseError:
    return SnapshotParseError(ErrorCode.MALFORMED_RECORD, f"{field}: {detail}")


def _require(data: dict[str, Any], field: str) -> Any:
    if field not in data or data[field] is None:
        raise _fail(field, "required field is missing")
    return data[field]


def _decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise _fail(field, f"not a decimal: {value!r}") from exc


def _optional_decimal(value: Any, field: str) -> Decimal | None:
    return None if value is None else _decimal(value, field)


def _datetime(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise _fail(field, f"not an ISO datetime: {value!r}") from exc
    if parsed.tzinfo is None:
        # The contracts require tz-aware datetimes; a naive one in a recording
        # is corruption, not something to localize on the reader's behalf.
        raise _fail(field, f"datetime is not timezone-aware: {value!r}")
    return parsed


def _optional_datetime(value: Any, field: str) -> datetime | None:
    return None if value is None else _datetime(value, field)


def _date(value: Any, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise _fail(field, f"not an ISO date: {value!r}") from exc


def _enum(enum_cls: Any, value: Any, field: str) -> Any:
    try:
        return enum_cls(value)
    except ValueError as exc:
        raise _fail(field, f"not a valid {enum_cls.__name__}: {value!r}") from exc


def _int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise _fail(field, f"not an integer: {value!r}") from exc


def _optional_int(value: Any, field: str) -> int | None:
    return None if value is None else _int(value, field)


def _float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise _fail(field, f"not a float: {value!r}") from exc


def _optional_float(value: Any, field: str) -> float | None:
    return None if value is None else _float(value, field)


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise _fail(field, f"expected a list, got {type(value).__name__}")
    return tuple(str(v) for v in value)


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _fail(field, f"expected an object, got {type(value).__name__}")
    return value


def _bar(data: Any, index: int) -> Bar:
    where = f"bars_1m[{index}]"
    body = _mapping(data, where)
    return Bar(
        timestamp=_datetime(_require(body, "timestamp"), f"{where}.timestamp"),
        open=_decimal(_require(body, "open"), f"{where}.open"),
        high=_decimal(_require(body, "high"), f"{where}.high"),
        low=_decimal(_require(body, "low"), f"{where}.low"),
        close=_decimal(_require(body, "close"), f"{where}.close"),
        volume=_int(body.get("volume", 0), f"{where}.volume"),
    )


def _contract(data: Any, where: str) -> OptionContract:
    body = _mapping(data, where)
    return OptionContract(
        contract_id=str(_require(body, "contract_id")),
        underlying_symbol=str(_require(body, "underlying_symbol")),
        expiration=_date(_require(body, "expiration"), f"{where}.expiration"),
        option_type=_enum(
            OptionType, _require(body, "option_type"), f"{where}.option_type"
        ),
        strike=_decimal(_require(body, "strike"), f"{where}.strike"),
        multiplier=_int(body.get("multiplier", 100), f"{where}.multiplier"),
        settlement_style=str(body.get("settlement_style", "PM")),
    )


def _quote(data: Any, index: int) -> OptionQuote:
    where = f"option_chain[{index}]"
    body = _mapping(data, where)
    return OptionQuote(
        contract=_contract(_require(body, "contract"), f"{where}.contract"),
        received_at=_datetime(_require(body, "received_at"), f"{where}.received_at"),
        source=str(_require(body, "source")),
        bid=_optional_decimal(body.get("bid"), f"{where}.bid"),
        ask=_optional_decimal(body.get("ask"), f"{where}.ask"),
        last=_optional_decimal(body.get("last"), f"{where}.last"),
        mark=_optional_decimal(body.get("mark"), f"{where}.mark"),
        volume=_optional_int(body.get("volume"), f"{where}.volume"),
        open_interest=_optional_int(body.get("open_interest"), f"{where}.open_interest"),
        implied_volatility=_optional_float(
            body.get("implied_volatility"), f"{where}.implied_volatility"
        ),
        delta=_optional_float(body.get("delta"), f"{where}.delta"),
        gamma=_optional_float(body.get("gamma"), f"{where}.gamma"),
        theta=_optional_float(body.get("theta"), f"{where}.theta"),
        vega=_optional_float(body.get("vega"), f"{where}.vega"),
        observed_at=_optional_datetime(body.get("observed_at"), f"{where}.observed_at"),
        age_seconds=_optional_float(body.get("age_seconds"), f"{where}.age_seconds"),
        quality_flags=_strings(body.get("quality_flags"), f"{where}.quality_flags"),
    )


def _observation(data: Any, index: int) -> FeedObservation:
    where = f"feed_observations[{index}]"
    body = _mapping(data, where)
    return FeedObservation(
        component=_enum(
            FeedComponent, _require(body, "component"), f"{where}.component"
        ),
        provider=str(_require(body, "provider")),
        received_at=_datetime(_require(body, "received_at"), f"{where}.received_at"),
        status=_enum(FeedStatus, _require(body, "status"), f"{where}.status"),
        freshness_limit_seconds=_float(
            body.get("freshness_limit_seconds", 0.0), f"{where}.freshness_limit_seconds"
        ),
        observed_at=_optional_datetime(body.get("observed_at"), f"{where}.observed_at"),
        age_seconds=_optional_float(body.get("age_seconds"), f"{where}.age_seconds"),
        attempt_order=_int(body.get("attempt_order", 0), f"{where}.attempt_order"),
        fallback_used=bool(body.get("fallback_used", False)),
        error_code=None if body.get("error_code") is None else str(body["error_code"]),
        error_message_hash=(
            None
            if body.get("error_message_hash") is None
            else str(body["error_message_hash"])
        ),
    )


def _selection(data: Any, index: int) -> ProviderSelection:
    where = f"selected_providers[{index}]"
    body = _mapping(data, where)
    return ProviderSelection(
        component=_enum(
            FeedComponent, _require(body, "component"), f"{where}.component"
        ),
        provider=str(_require(body, "provider")),
        fallback_used=bool(body.get("fallback_used", False)),
        attempt_order=_int(body.get("attempt_order", 0), f"{where}.attempt_order"),
    )


def _coverage(data: Any) -> ChainCoverage:
    body = _mapping(data, "chain_coverage")
    return ChainCoverage(
        contracts_total=_int(body.get("contracts_total", 0), "chain_coverage.contracts_total"),
        strikes_total=_int(body.get("strikes_total", 0), "chain_coverage.strikes_total"),
        min_strike=_optional_decimal(body.get("min_strike"), "chain_coverage.min_strike"),
        max_strike=_optional_decimal(body.get("max_strike"), "chain_coverage.max_strike"),
        has_calls=bool(body.get("has_calls", False)),
        has_puts=bool(body.get("has_puts", False)),
    )


def _catalyst(data: Any) -> CatalystState:
    body = _mapping(data, "catalyst_state")
    return CatalystState(
        lockout_active=bool(body.get("lockout_active", False)),
        reason=None if body.get("reason") is None else str(body["reason"]),
    )


def _quality(data: Any) -> DataQuality:
    body = _mapping(data, "data_quality")
    return DataQuality(
        is_healthy=bool(body.get("is_healthy", True)),
        penalty=_float(body.get("penalty", 0.0), "data_quality.penalty"),
        flags=_strings(body.get("flags"), "data_quality.flags"),
    )


def _term_structure(data: Any) -> VolatilityTermStructure | None:
    """Absent is ``None``; present-but-malformed still raises (fail-closed)."""
    if data is None:
        return None
    body = _mapping(data, "volatility_term_structure")
    return VolatilityTermStructure(
        vix=_float(_require(body, "vix"), "volatility_term_structure.vix"),
        vix9d=_optional_float(body.get("vix9d"), "volatility_term_structure.vix9d"),
        vix3m=_optional_float(body.get("vix3m"), "volatility_term_structure.vix3m"),
        vvix=_optional_float(body.get("vvix"), "volatility_term_structure.vvix"),
        vvix_baseline=_optional_float(
            body.get("vvix_baseline"), "volatility_term_structure.vvix_baseline"
        ),
        source=str(body.get("source", "")),
    )


def _breadth(data: Any) -> BreadthState | None:
    if data is None:
        return None
    body = _mapping(data, "breadth")
    return BreadthState(
        rsp_spy_div=_optional_float(body.get("rsp_spy_div"), "breadth.rsp_spy_div"),
        sector_align=_optional_float(body.get("sector_align"), "breadth.sector_align"),
        top10_pressure=_optional_float(
            body.get("top10_pressure"), "breadth.top10_pressure"
        ),
        sectors_observed=_int(body.get("sectors_observed", 0), "breadth.sectors_observed"),
        source=str(body.get("source", "")),
    )


def _sequence(data: Any, field: str) -> list[Any]:
    if data is None:
        return []
    if not isinstance(data, (list, tuple)):
        raise _fail(field, f"expected a list, got {type(data).__name__}")
    return list(data)


def snapshot_from_dict(data: dict[str, Any]) -> CanonicalMarketSnapshot:
    """Rebuild a snapshot from `to_canonical_json`'s output. Fail-closed."""
    if not isinstance(data, dict):
        raise _fail("snapshot", f"expected an object, got {type(data).__name__}")
    return CanonicalMarketSnapshot(
        snapshot_id=str(_require(data, "snapshot_id")),
        content_hash=str(_require(data, "content_hash")),
        timestamp=_datetime(_require(data, "timestamp"), "timestamp"),
        session_date=_date(_require(data, "session_date"), "session_date"),
        underlying_symbol=str(_require(data, "underlying_symbol")),
        underlying_price=_decimal(_require(data, "underlying_price"), "underlying_price"),
        session_status=_enum(
            SessionStatus, _require(data, "session_status"), "session_status"
        ),
        schema_version=str(data.get("schema_version", "")),
        normalization_version=str(data.get("normalization_version", NORMALIZATION_VERSION)),
        underlying_bid=_optional_decimal(data.get("underlying_bid"), "underlying_bid"),
        underlying_ask=_optional_decimal(data.get("underlying_ask"), "underlying_ask"),
        minutes_from_open=_optional_int(
            data.get("minutes_from_open"), "minutes_from_open"
        ),
        minutes_to_close=_optional_int(data.get("minutes_to_close"), "minutes_to_close"),
        bars_1m=tuple(
            _bar(item, i) for i, item in enumerate(_sequence(data.get("bars_1m"), "bars_1m"))
        ),
        option_chain=tuple(
            _quote(item, i)
            for i, item in enumerate(_sequence(data.get("option_chain"), "option_chain"))
        ),
        feed_observations=tuple(
            _observation(item, i)
            for i, item in enumerate(
                _sequence(data.get("feed_observations"), "feed_observations")
            )
        ),
        selected_providers=tuple(
            _selection(item, i)
            for i, item in enumerate(
                _sequence(data.get("selected_providers"), "selected_providers")
            )
        ),
        chain_coverage=_coverage(data.get("chain_coverage")),
        catalyst_state=_catalyst(data.get("catalyst_state")),
        data_quality=_quality(data.get("data_quality")),
        missing_components=_strings(
            data.get("missing_components"), "missing_components"
        ),
        volatility_term_structure=_term_structure(data.get("volatility_term_structure")),
        breadth=_breadth(data.get("breadth")),
    )
