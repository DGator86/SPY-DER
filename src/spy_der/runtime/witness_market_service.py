"""Network-enabled market runtime with frozen independent forecast witnesses.

The deterministic engine remains network-isolated. This front-of-pipeline
service samples Beta-spy at the same cadence as SPY-DER market snapshots and
writes a sibling integrity-checked artifact keyed to the market snapshot id.
Replay therefore sees the exact witness that was available at decision time.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from spy_der.forecasting.witnesses.beta import (
    BETA_WITNESS_VERSION,
    BetaStateClient,
    BetaWitnessError,
    BetaWitnessSnapshot,
)
from spy_der.market_data.composite import CompositeFeed
from spy_der.market_data.recording import build_record
from spy_der.runtime.artifacts import StageArtifactStore
from spy_der.runtime.market_service import (
    _DEFAULT_PROVIDERS,
    _DEFAULT_SETTLEMENT_PROVIDER,
    MarketService,
    MarketServiceConfig,
    _ensure_trailing_newline,
)

_DEFAULT_STATE_ROOT = "/var/lib/spy-der"
ENV_BETA_STATE_URL = "SPY_DER_BETA_STATE_URL"
ENV_BETA_MAX_AGE_SECONDS = "SPY_DER_BETA_MAX_AGE_SECONDS"

log = logging.getLogger("spy_der.market")


@dataclass
class WitnessMarketService(MarketService):
    """MarketService that freezes Beta forecast evidence beside each snapshot."""

    _beta_client: BetaStateClient | None = field(default=None, init=False, repr=False)
    _beta_store: StageArtifactStore | None = field(default=None, init=False, repr=False)

    def _client(self) -> BetaStateClient | None:
        if self._beta_client is not None:
            return self._beta_client
        url = os.environ.get(ENV_BETA_STATE_URL, "").strip()
        if not url:
            return None
        try:
            max_age = float(os.environ.get(ENV_BETA_MAX_AGE_SECONDS, "90"))
        except ValueError:
            max_age = 90.0
        self._beta_client = BetaStateClient(
            base_url=url,
            max_age_seconds=max(1.0, max_age),
        )
        return self._beta_client

    def _store(self) -> StageArtifactStore:
        if self._beta_store is None:
            self._beta_store = StageArtifactStore(self.config.state_root, "witnesses/beta")
        return self._beta_store

    @staticmethod
    def _witness_payload(witness: BetaWitnessSnapshot) -> dict[str, Any]:
        return {
            "source_timestamp": witness.source_timestamp.isoformat(),
            "status": witness.status,
            "stale_seconds": witness.stale_seconds,
            "coverage_ratio": witness.coverage_ratio,
            "covered_weight": witness.covered_weight,
            "source_version": witness.source_version,
            "horizons": [asdict(item) for item in witness.horizons],
        }

    def _record_beta(self, *, snapshot_id: str, session: str, as_of: datetime) -> None:
        client = self._client()
        if client is None:
            return
        available = False
        reason = ""
        witness_payload: dict[str, Any] | None = None
        try:
            witness = client.fetch(as_of=as_of)
            witness_payload = self._witness_payload(witness)
            available = True
        except BetaWitnessError as exc:
            # Beta is an optional independent witness. Its failure must never
            # interrupt canonical market collection or manufacture a fallback.
            reason = str(exc)
            log.warning("Beta witness unavailable for %s: %s", snapshot_id, exc)

        self._store().append(
            session,
            artifact_id=f"{snapshot_id}:beta",
            schema_version=BETA_WITNESS_VERSION,
            payload={
                "market_snapshot_id": snapshot_id,
                "captured_at": as_of.isoformat(),
                "available": available,
                "unavailable_reason": reason,
                "witness": witness_payload,
            },
        )

    def _tick(self, feed: CompositeFeed) -> None:
        """Record canonical market data, then freeze Beta as independent evidence."""
        now = datetime.now(tz=UTC)
        try:
            snapshot = feed.snapshot(now)
        except Exception:
            log.exception("snapshot failed; continuing")
            return
        if snapshot is None:
            log.warning("no provider returned a tick at %s", now.isoformat())
            return

        path = self.config.recording_path(snapshot.session_date)
        seq = self._next_seq(path)
        record = build_record(seq, snapshot)
        self._seq_by_session[path.stem] = seq + 1
        _ensure_trailing_newline(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            )
            handle.write("\n")

        self._record_beta(
            snapshot_id=snapshot.snapshot_id,
            session=snapshot.session_date.isoformat(),
            as_of=snapshot.timestamp,
        )
        log.info(
            "recorded %s source=%s quality=%.2f contracts=%d beta=%s",
            snapshot.snapshot_id,
            feed.last_source,
            1.0 - float(snapshot.data_quality.penalty),
            len(snapshot.option_chain),
            "configured" if self._client() is not None else "disabled",
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SPY-DER market-data ingestion")
    parser.add_argument("--state-root", default=_DEFAULT_STATE_ROOT)
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument(
        "--provider",
        action="append",
        dest="providers",
        help="Provider name, repeatable; order is the failover priority",
    )
    parser.add_argument(
        "--settlement-provider",
        default=_DEFAULT_SETTLEMENT_PROVIDER,
        help="dedicated settlement source; empty string disables it",
    )
    parser.add_argument("--max-ticks", type=int, default=0, help="0 = run until signalled")
    parser.add_argument("--config", default=None, help="reserved (not read yet)")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = build_arg_parser().parse_args(argv)
    if args.config:
        log.warning("--config is accepted but not read yet; using flags and environment")

    service = WitnessMarketService(
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
