"""VPS shadow runner — publishes SPY-DER parallel-track state for the dashboard.

When embedded in 0DTE's `shadow_runner`, prefer the in-process bridge
(`spy_der.integrations.zerodte`). This module is the standalone process that
writes `/var/lib/zerodte/spy_der_state.json` (or a configured path) so the
0DTE dashboard can merge SPY-DER into the parallel panel even if the
in-process provider is temporarily unavailable.
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

from spy_der.deployment.cutover import (
    CutoverApproval,
    activate_controlled_cutover,
)
from spy_der.integrations.zerodte.provider import (
    PARALLEL_TRACK_ID,
    PARALLEL_TRACK_LABEL,
)
from spy_der.runtime.state_writer import write_live_state_file

__all__ = ["RunnerConfig", "SpyDerVpsRunner", "build_arg_parser", "main"]

log = logging.getLogger("spy_der.runner")


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    live_state_path: str = "/var/lib/zerodte/spy_der_state.json"
    interval_seconds: float = 60.0
    account_id: str = "system_b_grok"
    approved_by: str = "repository-owner"
    approval_note: str = "Phase 17 activate."


@dataclass
class SpyDerVpsRunner:
    config: RunnerConfig
    _stop: bool = False

    def request_stop(self, *_args: object) -> None:
        self._stop = True

    def run_forever(self) -> None:
        log.info(
            "SPY-DER VPS runner starting path=%s interval=%.1fs",
            self.config.live_state_path,
            self.config.interval_seconds,
        )
        approval = CutoverApproval(
            approved_by=self.config.approved_by,
            approved_at=datetime.now(tz=UTC),
            approval_note=self.config.approval_note,
            phase="phase-17",
        )
        cutover = activate_controlled_cutover(approval=approval)
        while not self._stop:
            payload = self._heartbeat_payload(cutover.snapshot().as_dict())
            write_live_state_file(self.config.live_state_path, payload)
            time.sleep(self.config.interval_seconds)
        log.info("SPY-DER VPS runner stopped")

    def _heartbeat_payload(self, cutover: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(tz=UTC).isoformat()
        return {
            "schema_version": "spy_der.parallel.v1",
            "generated_at": now,
            "track": PARALLEL_TRACK_ID,
            "label": PARALLEL_TRACK_LABEL,
            "mode": "shadow",
            "role": "ai_decision_maker",
            "account_id": self.config.account_id,
            "live_execution_enabled": False,
            "cutover": cutover,
            "parallel": {
                "track": PARALLEL_TRACK_ID,
                "label": PARALLEL_TRACK_LABEL,
                "source": "spy_der",
                "mode": "shadow",
                "action": "IDLE",
                "structure": None,
                "direction": None,
                "confidence": None,
                "uncertainty": None,
                "size_cap": None,
                "note": (
                    "heartbeat — decisions are published in-process via "
                    "0DTE shadow_runner when spy_der is installed"
                ),
            },
            "system": {
                "status": "heartbeat",
                "note": "standalone runner alive; live trading disabled",
            },
        }


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SPY-DER VPS parallel-track runner")
    p.add_argument(
        "--live-state",
        default="/var/lib/zerodte/spy_der_state.json",
        help="Output JSON path merged/read by 0DTE dashboard",
    )
    p.add_argument("--interval", type=float, default=60.0)
    p.add_argument("--account-id", default="system_b_grok")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = build_arg_parser().parse_args(argv)
    cfg = RunnerConfig(
        live_state_path=args.live_state,
        interval_seconds=args.interval,
        account_id=args.account_id,
    )
    runner = SpyDerVpsRunner(config=cfg)
    signal.signal(signal.SIGINT, runner.request_stop)
    signal.signal(signal.SIGTERM, runner.request_stop)
    Path(cfg.live_state_path).parent.mkdir(parents=True, exist_ok=True)
    runner.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
