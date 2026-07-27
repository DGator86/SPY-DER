"""Market runtime — the front of the SPY-DER pipeline (`spy-der market`).

Drives `spy-der-market.service`: poll the configured provider chain, canonicalize
each tick, and append it to the session recording under the state root. This is
the process that makes SPY-DER's market ownership real rather than declared.

It writes and nothing else — no features, no forecasts, no decisions. Downstream
stages read the recording, so a bad tick is inspectable after the fact instead of
having already propagated.

Fail-closed behavior: when no provider in the chain is configured, the service
reports that and exits non-zero rather than idling as a healthy-looking unit that
records nothing.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spy_der.market_data.calendar import MarketCalendar
from spy_der.market_data.composite import CompositeFeed
from spy_der.market_data.providers.factory import (
    UnknownProviderError,
    build_provider_chain,
    build_settlement_provider,
)
from spy_der.market_data.recording import build_record
from spy_der.runtime.heartbeat import write_heartbeat

__all__ = ["MarketService", "MarketServiceConfig", "build_arg_parser", "main"]

log = logging.getLogger("spy_der.market")

#: Ordered failover. Tradier leads because a brokerage account returns real-time
#: option NBBO with greeks, where the Massive snapshot plan routinely has no
#: `last_quote` and falls back to `day.close` — a chain the execution guard will
#: refuse to trade on anyway. Unconfigured providers are skipped, not fatal, so a
#: host holding either key alone still runs.
_DEFAULT_PROVIDERS = ("tradier", "massive")

#: Settlement's dedicated source, separate from the failover chain.
#:
#: Without one, `settlement` is a required-but-missing component on every tick
#: and the snapshot carries a 0.5 quality penalty — below the configured
#: `min_data_quality`, so the deterministic layers refuse to trade. Yahoo needs
#: no credential, so the default is a working settlement source rather than a
#: standing penalty that has to be explained.
_DEFAULT_SETTLEMENT_PROVIDER = "yahoo"


@dataclass(frozen=True, slots=True)
class MarketServiceConfig:
    state_root: str = "/var/lib/spy-der"
    symbol: str = "SPY"
    interval_seconds: float = 60.0
    providers: tuple[str, ...] = _DEFAULT_PROVIDERS
    #: Settlement's dedicated source; empty disables the backstop.
    settlement_provider: str = _DEFAULT_SETTLEMENT_PROVIDER
    #: Stop after this many ticks. 0 means run until signalled (the unit's case).
    max_ticks: int = 0

    @property
    def market_dir(self) -> Path:
        return Path(self.state_root) / "market"

    def recording_path(self, session: datetime) -> Path:
        return self.market_dir / f"{session.date().isoformat()}.jsonl"


def _last_seq(path: Path) -> int:
    """Highest sequence already recorded in ``path``; ``-1`` when there is none.

    Tolerant by design. A recording whose tail is unreadable — a crash mid-write
    leaves a partial final line — still yields the highest *parsable* sequence,
    so the service resumes rather than restarting at 0 and guaranteeing a gap.
    """
    if not path.is_file():
        return -1
    highest = -1
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    seq = int(json.loads(line)["seq"])
                except (ValueError, TypeError, KeyError):
                    continue
                highest = max(highest, seq)
    except OSError as exc:
        # Unreadable is not the same as empty; refusing to guess a sequence is
        # safer than appending one that may collide.
        log.error("cannot read %s to resume its sequence: %s", path, exc)
        raise
    return highest


def _ensure_trailing_newline(path: Path) -> None:
    """Terminate a partial final line before appending after it.

    A crash mid-write leaves a record with no trailing newline. Appending then
    concatenates the next record onto that fragment, producing one unparseable
    line *and* losing the new record. Closing the fragment first costs one byte
    and confines the damage to the record that was actually interrupted.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return
    with path.open("rb+") as handle:
        handle.seek(-1, 2)
        if handle.read(1) != b"\n":
            handle.write(b"\n")


@dataclass
class MarketService:
    """Poll providers, canonicalize, append to the session recording."""

    config: MarketServiceConfig
    _stop: bool = False
    #: Next sequence number per session file, resumed from disk on first touch.
    _seq_by_session: dict[str, int] = field(default_factory=dict)

    def request_stop(self, *_args: object) -> None:
        self._stop = True

    def _next_seq(self, path: Path) -> int:
        """Sequence number for the next record appended to ``path``.

        Resumed from the file rather than counted in memory. The recording is
        append-only and `ReplayFeed` requires a gap-free sequence, so a counter
        that restarted at 0 would corrupt the whole session the first time this
        process appended to a file it did not create — which happens on every
        `Restart=always` recovery mid-session, and on the day a 0DTE import is
        followed by SPY-DER taking over collection.

        Only the last line is parsed: the file is written one record per line
        with a monotonic sequence, so the tail is the whole answer and a full
        read would cost the entire session on every restart.
        """
        session = path.stem
        cached = self._seq_by_session.get(session)
        if cached is not None:
            return cached
        self._seq_by_session[session] = _last_seq(path) + 1
        return self._seq_by_session[session]

    def run(self) -> int:
        cfg = self.config
        try:
            chain = build_provider_chain(list(cfg.providers), symbol=cfg.symbol)
        except UnknownProviderError as exc:
            log.error("provider configuration error: %s", exc)
            self._publish_failure(
                detail=f"provider configuration error: {exc}",
                extra={"error": "unknown_provider", "providers": str(exc)},
            )
            return 2

        if chain.is_empty:
            # A unit that starts cleanly and records nothing is worse than one
            # that fails: the dashboard would look alive with no data behind it.
            # Publish a failed heartbeat so /v1/system shows the credential miss
            # instead of the ambiguous never_seen state.
            detail = (
                "no configured market-data provider "
                f"({chain.describe()}) — set TRADIER_ACCESS_TOKEN and/or "
                "MASSIVE_API_KEY in /etc/spy-der/spy-der.env"
            )
            log.error("%s", detail)
            self._publish_failure(
                detail=detail,
                extra={
                    "error": "no_credential",
                    "providers": chain.describe(),
                },
            )
            return 3

        log.info(
            "market runtime starting: %s interval=%.1fs",
            chain.describe(),
            cfg.interval_seconds,
        )
        settlement = build_settlement_provider(
            cfg.settlement_provider, symbol=cfg.symbol
        )
        if settlement is not None:
            log.info("settlement provider: %s", settlement.name)
        else:
            # Not fatal, but it does mean every snapshot fails the quality floor.
            log.warning(
                "no settlement provider: snapshots will report settlement missing"
            )
        feed = CompositeFeed(
            list(chain.providers),
            settlement_provider=settlement,
            calendar=MarketCalendar(),
        )
        cfg.market_dir.mkdir(parents=True, exist_ok=True)

        ticks = 0
        while not self._stop:
            self._tick(feed)
            ticks += 1
            write_heartbeat(
                cfg.state_root,
                "market",
                interval_seconds=cfg.interval_seconds,
                detail=f"{ticks} tick(s) this run",
                extra={"providers": chain.describe(), "ticks": ticks},
            )
            if cfg.max_ticks and ticks >= cfg.max_ticks:
                break
            if self._stop:
                break
            time.sleep(cfg.interval_seconds)

        log.info("market runtime stopped after %d tick(s)", ticks)
        return 0

    def _publish_failure(self, *, detail: str, extra: dict[str, Any] | None = None) -> None:
        """Surface a start failure on the dashboard before exiting.

        Without this, Restart=always crash-loops leave market as never_seen —
        indistinguishable from a unit that was never installed.
        """
        payload = {"health": "failed"}
        if extra:
            payload.update(extra)
        write_heartbeat(
            self.config.state_root,
            "market",
            interval_seconds=self.config.interval_seconds,
            detail=detail,
            extra=payload,
        )

    def _tick(self, feed: CompositeFeed) -> None:
        now = datetime.now(tz=UTC)
        try:
            snapshot = feed.snapshot(now)
        # Deliberately broad: one bad tick must not take down the front of
        # the pipeline. The failure is logged with a traceback and skipped.
        except Exception:
            log.exception("snapshot failed; continuing")
            return
        if snapshot is None:
            log.warning("no provider returned a tick at %s", now.isoformat())
            return

        path = self.config.recording_path(now)
        seq = self._next_seq(path)
        record = build_record(seq, snapshot)
        self._seq_by_session[path.stem] = seq + 1
        _ensure_trailing_newline(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            )
            handle.write("\n")
        log.info(
            "recorded %s source=%s quality=%.2f contracts=%d",
            snapshot.snapshot_id,
            feed.last_source,
            1.0 - float(snapshot.data_quality.penalty),
            len(snapshot.option_chain),
        )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SPY-DER market-data ingestion")
    p.add_argument("--state-root", default="/var/lib/spy-der")
    p.add_argument("--symbol", default="SPY")
    p.add_argument("--interval", type=float, default=60.0)
    p.add_argument(
        "--provider",
        action="append",
        dest="providers",
        help="Provider name, repeatable; order is the failover priority",
    )
    p.add_argument(
        "--settlement-provider",
        default=_DEFAULT_SETTLEMENT_PROVIDER,
        help="dedicated settlement source; empty string disables it",
    )
    p.add_argument("--max-ticks", type=int, default=0, help="0 = run until signalled")
    # Accepted so the systemd unit's --config is not a hard error before the
    # config loader lands; the file is not read yet.
    p.add_argument("--config", default=None, help="reserved (not read yet)")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = build_arg_parser().parse_args(argv)
    if args.config:
        log.warning("--config is accepted but not read yet; using flags and environment")

    service = MarketService(
        config=MarketServiceConfig(
            state_root=args.state_root,
            symbol=args.symbol,
            interval_seconds=args.interval,
            providers=tuple(args.providers or _DEFAULT_PROVIDERS),
            settlement_provider=args.settlement_provider,
            max_ticks=max(args.max_ticks, 0),
        )
    )
    signal.signal(signal.SIGINT, service.request_stop)
    signal.signal(signal.SIGTERM, service.request_stop)
    return service.run()


if __name__ == "__main__":
    raise SystemExit(main())
