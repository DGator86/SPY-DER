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
import logging
import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spy_der.market_data.calendar import MarketCalendar
from spy_der.market_data.composite import CompositeFeed
from spy_der.market_data.providers.factory import (
    UnknownProviderError,
    build_provider_chain,
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


@dataclass(frozen=True, slots=True)
class MarketServiceConfig:
    state_root: str = "/var/lib/spy-der"
    symbol: str = "SPY"
    interval_seconds: float = 60.0
    providers: tuple[str, ...] = _DEFAULT_PROVIDERS
    #: Stop after this many ticks. 0 means run until signalled (the unit's case).
    max_ticks: int = 0

    @property
    def market_dir(self) -> Path:
        return Path(self.state_root) / "market"

    def recording_path(self, session: datetime) -> Path:
        return self.market_dir / f"{session.date().isoformat()}.jsonl"


@dataclass
class MarketService:
    """Poll providers, canonicalize, append to the session recording."""

    config: MarketServiceConfig
    _stop: bool = False
    _seq: int = 0

    def request_stop(self, *_args: object) -> None:
        self._stop = True

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
        feed = CompositeFeed(list(chain.providers), calendar=MarketCalendar())
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

        record = build_record(self._seq, snapshot)
        self._seq += 1
        path = self.config.recording_path(now)
        with path.open("a", encoding="utf-8") as handle:
            import json

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
            max_ticks=max(args.max_ticks, 0),
        )
    )
    signal.signal(signal.SIGINT, service.request_stop)
    signal.signal(signal.SIGTERM, service.request_stop)
    return service.run()


if __name__ == "__main__":
    raise SystemExit(main())
