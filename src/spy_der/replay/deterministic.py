from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from spy_der.contracts import JournalEvent
from spy_der.contracts.serialization import to_canonical_json


@dataclass(frozen=True, slots=True)
class ReplayInputManifest:
    timestamp: str
    market_snapshot_hash: str
    underlying_bars_hash: str
    option_chain_hash: str
    candidate_universe_hash: str
    fees_hash: str
    slippage_hash: str
    fill_assumptions_hash: str
    account_size_hash: str
    risk_ceilings_hash: str
    exit_policy_hash: str
    settlement_hash: str
    schema_version: str = "1.0.0"


class ManifestMismatchError(ValueError):
    """Raised when manifests differ and comparison is invalid."""


def ensure_matching_manifests(a: ReplayInputManifest, b: ReplayInputManifest) -> None:
    if a != b:
        msg = "cannot compare adapters with different replay input manifests"
        raise ManifestMismatchError(msg)


def journal_hash(events: tuple[JournalEvent, ...]) -> str:
    payload = to_canonical_json(events)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def deterministic_events(seed: str) -> tuple[JournalEvent, ...]:
    ts = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    return (
        JournalEvent(event_id=f"{seed}-1", timestamp=ts, event_type="replay.start"),
        JournalEvent(event_id=f"{seed}-2", timestamp=ts, event_type="replay.end"),
    )
