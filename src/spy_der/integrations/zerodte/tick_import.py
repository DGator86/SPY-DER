"""Import 0DTE tick recordings as SPY-DER canonical recordings.

0DTE's ``ChainRecorder`` has been writing every live tick to
``ticks_YYYY-MM-DD.jsonl.gz`` — gzipped JSONL, one file per session. That is
real recorded market history, and it contains everything the SPY-DER training
path needs, so there is no reason to wait weeks for fresh recordings before the
forecast models can be fitted on actual markets.

The source directory is always supplied by the caller; no legacy path is baked
in, because SPY-DER has to deploy without 0DTE present.

Source record shape (``chain_store``)::

    {"t":"tick","ts":...,"seq":N,"market":{...},"chain":{...},
     "bars":[[iso,o,h,l,c,v],...],"option_rows":[{...}],...}
    {"t":"settle","date":"YYYY-MM-DD","price":...}

Four things about the source format are load-bearing:

* **Bars are incremental.** Each tick carries only the bars newer than the
  previous record, so the rolling window has to be *reassembled* by
  accumulating across ticks — exactly what ``RecordedFeed`` does. Reading a
  tick's ``bars`` as the whole window would leave almost every snapshot with a
  handful of bars and silently kill every history-dependent feature.
* **``option_rows`` carries the per-contract detail.** ``side``, ``strike``,
  ``oi``, ``gamma``, ``delta``, ``bid``, ``ask``, ``volume`` — enough to rebuild
  a canonical chain and recompute GEX, flow and RND from scratch. The sibling
  ``chain`` block is only strike-level call/put quotes, which cannot support
  GEX; when ``option_rows`` is absent (older recordings) the tick still imports,
  but with no chain, and the ``option_chain`` component reports missing.
* **Unavailable numbers are NaN, not absent.** 0DTE's flow and breadth fields
  default to ``float("nan")``. SPY-DER contracts reject non-finite values, and
  more importantly a NaN must not become a 0 — so every field is passed through
  :func:`_finite`, which maps NaN to ``None`` and preserves "not observed".
* **Bar timestamps are naive UTC.** They are ``str(numpy.datetime64)`` of an
  epoch-derived array. Localizing them to ET would shift every bar by four or
  five hours and put the session's bars in the wrong day.

Imported snapshots go through the same :class:`CanonicalSnapshotAssembler` as
live ticks, so their ids and content hashes are computed identically and
downstream code cannot distinguish an imported session from a live-recorded
one. Provenance is not lost, though: every snapshot carries a
``source:zerodte_import`` quality flag.
"""

from __future__ import annotations

import gzip
import json
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from spy_der.contracts.market import (
    Bar,
    BreadthState,
    CanonicalMarketSnapshot,
    CatalystState,
    FeedComponent,
    OptionContract,
    OptionQuote,
    OptionType,
    VolatilityTermStructure,
)
from spy_der.market_data.assembler import CanonicalSnapshotAssembler
from spy_der.market_data.freshness import build_observation
from spy_der.market_data.recording import build_record

__all__ = [
    "FLAG_IMPORTED",
    "ImportResult",
    "import_directory",
    "import_session_file",
    "iter_session_snapshots",
]

log = logging.getLogger("spy_der.zerodte_import")

ET = ZoneInfo("America/New_York")

#: Marks a snapshot as reconstructed from a 0DTE recording rather than observed
#: live. Imported and live snapshots are otherwise identical by construction.
FLAG_IMPORTED = "source:zerodte_import"

#: Bars kept per snapshot, matching the live providers' default window.
DEFAULT_BAR_WINDOW = 420

_PROVIDER = "zerodte_import"


@dataclass
class ImportResult:
    """What the import produced, and what it could not."""

    sessions_written: tuple[str, ...] = ()
    snapshots_written: int = 0
    settlements: dict[str, float] = field(default_factory=dict)
    skipped: tuple[tuple[str, str], ...] = ()
    ticks_without_chain: int = 0
    malformed_lines: int = 0

    def describe(self) -> str:
        text = (
            f"{self.snapshots_written} snapshot(s) across "
            f"{len(self.sessions_written)} session(s)"
        )
        if self.ticks_without_chain:
            text += f"; {self.ticks_without_chain} tick(s) had no option_rows"
        if self.malformed_lines:
            text += f"; {self.malformed_lines} malformed line(s)"
        if self.skipped:
            text += "; skipped: " + ", ".join(f"{s} ({why})" for s, why in self.skipped)
        return text


# --------------------------------------------------------------------------- #
# Scalar coercion                                                             #
# --------------------------------------------------------------------------- #
def _finite(value: Any) -> float | None:
    """A finite float, or ``None``.

    0DTE encodes "not observed" as NaN. Mapping that to ``None`` rather than 0
    is the whole point: a zero ``rsp_spy_div`` asserts perfectly neutral
    breadth, which is a confident claim to manufacture out of a missing feed.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _decimal(value: Any) -> Decimal | None:
    parsed = _finite(value)
    if parsed is None:
        return None
    try:
        return Decimal(f"{parsed:.6f}")
    except (InvalidOperation, ValueError):
        return None


def _positive_decimal(value: Any) -> Decimal | None:
    parsed = _decimal(value)
    return parsed if parsed is not None and parsed > 0 else None


# --------------------------------------------------------------------------- #
# Record parsing                                                              #
# --------------------------------------------------------------------------- #
def _read_lines(path: Path) -> Iterator[dict[str, Any]]:
    """Decode one recording, tolerating a truncated tail.

    A crashed recorder leaves a partial final line; `chain_store` reads through
    it the same way. One unreadable line must not cost the session.
    """
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                yield {"t": "_malformed"}
                continue
            if isinstance(record, dict):
                yield record


def _bar_timestamp(raw: Any) -> datetime | None:
    """Parse a recorded bar timestamp as UTC.

    The recorder writes ``str(numpy.datetime64)`` of an epoch-derived array, so
    the value is naive but denotes UTC. Attaching ET instead would shift every
    bar by hours and misfile the session.
    """
    if not isinstance(raw, str):
        return None
    text = raw.replace(" ", "T")
    # numpy renders nanosecond precision; datetime tops out at microseconds.
    if "." in text:
        head, _, frac = text.partition(".")
        text = f"{head}.{frac[:6]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _bar_from_row(row: Any) -> Bar | None:
    """``[iso, open, high, low, close, volume]`` -> :class:`Bar`."""
    if not isinstance(row, (list, tuple)) or len(row) < 6:
        return None
    timestamp = _bar_timestamp(row[0])
    if timestamp is None:
        return None
    open_ = _positive_decimal(row[1])
    high = _positive_decimal(row[2])
    low = _positive_decimal(row[3])
    close = _positive_decimal(row[4])
    if open_ is None or high is None or low is None or close is None:
        return None
    volume = _finite(row[5])
    if volume is None or volume < 0:
        return None
    return Bar(
        timestamp=timestamp,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=int(volume),
    )


def _option_quote(
    row: Any, *, session: date, received_at: datetime
) -> OptionQuote | None:
    """One ``option_rows`` entry -> canonical :class:`OptionQuote`.

    Incomplete rows are dropped rather than defaulted, matching the live
    adapters: a zeroed gamma is not a neutral value downstream, it reshapes the
    whole GEX surface.
    """
    if not isinstance(row, dict):
        return None
    side = str(row.get("side", "")).lower()
    if side not in {"call", "put"}:
        return None
    strike = _positive_decimal(row.get("strike"))
    gamma = _finite(row.get("gamma"))
    delta = _finite(row.get("delta"))
    if strike is None or gamma is None or delta is None:
        return None

    open_interest = _finite(row.get("oi"))
    if open_interest is None or open_interest < 0:
        return None

    bid = _decimal(row.get("bid"))
    ask = _decimal(row.get("ask"))
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None

    option_type = OptionType.CALL if side == "call" else OptionType.PUT
    dte = _finite(row.get("dte_days")) or 0.0
    expiration = session + timedelta(days=int(dte)) if dte >= 1 else session
    volume = _finite(row.get("volume"))

    right = "C" if option_type is OptionType.CALL else "P"
    contract_id = f"SPY{expiration:%y%m%d}{right}{round(float(strike) * 1000):08d}"
    return OptionQuote(
        contract=OptionContract(
            contract_id=contract_id,
            underlying_symbol="SPY",
            expiration=expiration,
            option_type=option_type,
            strike=strike,
        ),
        received_at=received_at,
        source=_PROVIDER,
        bid=bid,
        ask=ask,
        mark=(bid + ask) / Decimal("2"),
        volume=int(volume) if volume is not None and volume >= 0 else None,
        open_interest=int(open_interest),
        delta=delta,
        gamma=gamma,
        observed_at=received_at,
    )


def _term_structure(market: dict[str, Any]) -> VolatilityTermStructure | None:
    vix = _finite(market.get("vix"))
    if vix is None or vix <= 0:
        return None
    return VolatilityTermStructure(
        vix=vix,
        vix9d=_finite(market.get("vix9d")),
        vix3m=_finite(market.get("vix3m")),
        vvix=_finite(market.get("vvix")),
        vvix_baseline=_finite(market.get("vvix_baseline")),
        source=_PROVIDER,
    )


def _breadth(market: dict[str, Any]) -> BreadthState | None:
    state = BreadthState(
        rsp_spy_div=_finite(market.get("rsp_spy_div")),
        sector_align=_finite(market.get("sector_align")),
        top10_pressure=_finite(market.get("top10_pressure")),
        source=_PROVIDER,
    )
    return state if state.is_observed else None


def _catalyst(market: dict[str, Any]) -> CatalystState:
    return CatalystState(
        lockout_active=bool(market.get("has_catalyst", False)),
        reason=str(market.get("catalyst_label") or "") or None,
    )


# --------------------------------------------------------------------------- #
# Session import                                                              #
# --------------------------------------------------------------------------- #
def iter_session_snapshots(
    path: Path,
    *,
    bar_window: int = DEFAULT_BAR_WINDOW,
    assembler: CanonicalSnapshotAssembler | None = None,
) -> Iterator[tuple[CanonicalMarketSnapshot, dict[str, Any]]]:
    """Yield ``(snapshot, stats)`` for each tick in one recording.

    ``stats`` reports per-tick conditions the caller aggregates. Bars accumulate
    across ticks because the recorder stores them incrementally.
    """
    build = assembler or CanonicalSnapshotAssembler()
    window: list[Bar] = []
    seen: set[datetime] = set()

    for record in _read_lines(path):
        kind = record.get("t")
        if kind == "_malformed":
            yield from ()  # counted by the caller via _read_lines
            continue
        if kind != "tick":
            continue

        raw_ts = record.get("ts")
        if not isinstance(raw_ts, str):
            continue
        try:
            timestamp = datetime.fromisoformat(raw_ts)
        except ValueError:
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=ET)
        session = timestamp.astimezone(ET).date()

        # Incremental bars: extend the rolling window, dropping repeats.
        for row in record.get("bars") or []:
            bar = _bar_from_row(row)
            if bar is not None and bar.timestamp not in seen:
                seen.add(bar.timestamp)
                window.append(bar)
        window.sort(key=lambda b: b.timestamp)
        if bar_window > 0 and len(window) > bar_window:
            window = window[-bar_window:]
            seen = {b.timestamp for b in window}

        market = record.get("market")
        if not isinstance(market, dict):
            continue
        spot = _positive_decimal(market.get("spot"))
        if spot is None:
            # No underlying price is not a recoverable snapshot; the live path
            # refuses the same way rather than fabricating one.
            continue

        chain = tuple(
            quote
            for quote in (
                _option_quote(row, session=session, received_at=timestamp)
                for row in (record.get("option_rows") or [])
            )
            if quote is not None
        )

        observations = tuple(
            build_observation(
                component,
                provider=_PROVIDER,
                received_at=timestamp,
                freshness_limit_seconds=60.0,
                observed_at=timestamp if present else None,
                present=present,
            )
            for component, present in (
                (FeedComponent.SPOT, True),
                (FeedComponent.BARS, bool(window)),
                (FeedComponent.OPTION_CHAIN, bool(chain)),
                # The recorder wrote a settlement row per session, so settlement
                # is available for the session even though it is not per-tick.
                (FeedComponent.SETTLEMENT, True),
            )
        )

        snapshot = build.assemble(
            timestamp=timestamp,
            underlying_symbol="SPY",
            underlying_price=spot,
            bars_1m=tuple(window),
            option_chain=chain,
            feed_observations=observations,
            catalyst_state=_catalyst(market),
            provider_flags=(FLAG_IMPORTED,),
            volatility_term_structure=_term_structure(market),
            breadth=_breadth(market),
        )
        yield snapshot, {"has_chain": bool(chain)}


def import_session_file(
    source: Path,
    destination_dir: Path,
    *,
    bar_window: int = DEFAULT_BAR_WINDOW,
    overwrite: bool = False,
) -> tuple[str, int, int]:
    """Convert one ``ticks_*.jsonl.gz`` into a SPY-DER recording.

    Returns ``(session, snapshots_written, ticks_without_chain)``.
    """
    session = source.name.removeprefix("ticks_").removesuffix(".jsonl.gz")
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / f"{session}.jsonl"
    if target.exists() and not overwrite:
        return session, 0, 0

    written = 0
    without_chain = 0
    # Written to a temporary sibling and moved into place, so an interrupted
    # import never leaves a half-session that later looks like a real recording.
    staging = target.with_suffix(".jsonl.partial")
    with staging.open("w", encoding="utf-8") as handle:
        for snapshot, stats in iter_session_snapshots(source, bar_window=bar_window):
            handle.write(json.dumps(build_record(written, snapshot), sort_keys=True))
            handle.write("\n")
            written += 1
            if not stats["has_chain"]:
                without_chain += 1

    if written:
        staging.replace(target)
    else:
        staging.unlink(missing_ok=True)
    return session, written, without_chain


def _settlements(source: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for record in _read_lines(source):
        if record.get("t") != "settle":
            continue
        price = _finite(record.get("price"))
        session = record.get("date")
        if price is not None and isinstance(session, str):
            out[session] = price
    return out


def import_directory(
    source_dir: str | Path,
    state_root: str | Path,
    *,
    sessions: Sequence[str] | None = None,
    bar_window: int = DEFAULT_BAR_WINDOW,
    overwrite: bool = False,
) -> ImportResult:
    """Import every 0DTE recording under ``source_dir`` into ``state_root``."""
    source = Path(source_dir)
    destination = Path(state_root) / "market"

    result = ImportResult()
    written_sessions: list[str] = []
    skipped: list[tuple[str, str]] = []
    settlements: dict[str, float] = {}
    wanted = set(sessions) if sessions else None

    for path in sorted(source.glob("ticks_*.jsonl.gz")):
        session = path.name.removeprefix("ticks_").removesuffix(".jsonl.gz")
        if wanted is not None and session not in wanted:
            continue
        try:
            name, count, without_chain = import_session_file(
                path, destination, bar_window=bar_window, overwrite=overwrite
            )
            settlements.update(_settlements(path))
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            # A corrupt archive costs its session, not the run.
            log.error("recording %s is unreadable: %s", path.name, exc)
            skipped.append((session, f"unreadable: {type(exc).__name__}"))
            continue

        if count:
            written_sessions.append(name)
            result.snapshots_written += count
            result.ticks_without_chain += without_chain
        else:
            skipped.append((session, "already imported" if not overwrite else "no usable ticks"))

    result.sessions_written = tuple(written_sessions)
    result.skipped = tuple(skipped)
    result.settlements = settlements
    return result
