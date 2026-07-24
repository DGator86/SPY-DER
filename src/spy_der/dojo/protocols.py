"""Experience-provider protocols — the Dojo's only window into 0DTE.

SPY-DER owns AI evaluation, curriculum, retention, and promotion gates.
0DTE owns recorded markets, candidate generation, settlements, and synthetic
market generation. Implementations of these protocols may read 0DTE files or
call 0DTE APIs; the Dojo itself must never import 0DTE internals.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from typing import Protocol

from spy_der.contracts.integration import MarketPacket, OutcomePacket

__all__ = [
    "CandidateEvaluator",
    "DecisionRecord",
    "EvaluationReport",
    "MarketExperienceProvider",
    "SyntheticUniverseProvider",
    "UniverseSpec",
]


class UniverseSpec(Protocol):
    """Opaque universe specification produced by a SyntheticUniverseProvider."""

    @property
    def universe_id(self) -> str: ...

    @property
    def archetype(self) -> str: ...


class DecisionRecord(Protocol):
    snapshot_id: str
    action: str
    candidate_id: str | None


class EvaluationReport(Protocol):
    """Opaque evaluation summary returned by CandidateEvaluator."""

    @property
    def status(self) -> str: ...

    def to_dict(self) -> dict[str, object]: ...


class MarketExperienceProvider(Protocol):
    """Recorded 0DTE market experience for Dojo / Sequential Dojo."""

    def sessions(self) -> Sequence[date]: ...

    def snapshots(self, session: date) -> Iterable[MarketPacket]: ...

    def outcome(self, snapshot_id: str) -> OutcomePacket | None: ...


class SyntheticUniverseProvider(Protocol):
    """Synthetic market snapshots owned/exposed by 0DTE."""

    def generate(self, specification: UniverseSpec) -> Iterable[MarketPacket]: ...


class CandidateEvaluator(Protocol):
    """Evaluate SPY-DER decisions against 0DTE outcomes / strategy backtests."""

    def evaluate(
        self,
        decisions: Sequence[DecisionRecord],
        outcomes: Sequence[OutcomePacket],
    ) -> EvaluationReport: ...
