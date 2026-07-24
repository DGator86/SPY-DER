"""Filesystem MarketExperienceProvider — reads versioned MarketPacket JSON.

Initial ownership boundary: SPY-DER consumes 0DTE experience through these
files (or later an API). It does not import 0DTE chain_store / journal.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from spy_der.contracts.integration import (
    MarketPacket,
    OutcomePacket,
    market_packet_from_dict,
    outcome_packet_from_dict,
)

__all__ = ["FileMarketExperienceProvider"]


class FileMarketExperienceProvider:
    """Directory layout::

        root/
          sessions.json          optional explicit session list
          snapshots/*.json       MarketPacket documents
          outcomes/*.json        OutcomePacket documents keyed by snapshot_id
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._snapshots_dir = self.root / "snapshots"
        self._outcomes_dir = self.root / "outcomes"

    def sessions(self) -> list[date]:
        sessions_file = self.root / "sessions.json"
        if sessions_file.is_file():
            with open(sessions_file, encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, list):
                return sorted({date.fromisoformat(str(item)) for item in raw})
        found: set[date] = set()
        for path in self._snapshot_paths():
            packet = self._load_market(path)
            if packet is not None:
                found.add(packet.session_date)
        return sorted(found)

    def snapshots(self, session: date) -> Iterable[MarketPacket]:
        for path in self._snapshot_paths():
            packet = self._load_market(path)
            if packet is not None and packet.session_date == session:
                yield packet

    def outcome(self, snapshot_id: str) -> OutcomePacket | None:
        path = self._outcomes_dir / f"{snapshot_id}.json"
        if not path.is_file():
            # Also allow a flat outcomes.jsonl later; for now miss → None.
            return None
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return None
        return outcome_packet_from_dict(data)

    def _snapshot_paths(self) -> list[Path]:
        if not self._snapshots_dir.is_dir():
            return []
        return sorted(self._snapshots_dir.glob("*.json"))

    def _load_market(self, path: Path) -> MarketPacket | None:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                return None
            return market_packet_from_dict(data)
        except (OSError, ValueError, KeyError, TypeError):
            return None
