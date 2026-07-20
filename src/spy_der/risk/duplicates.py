"""Duplicate order / equivalent position / stale-decision guards."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

__all__ = [
    "DecisionFreshness",
    "DuplicateGuard",
    "decision_signature",
    "is_stale_decision",
]


@dataclass(frozen=True, slots=True)
class DecisionFreshness:
    stale: bool
    age_seconds: float
    ttl_seconds: int
    reason: str = ""


def decision_signature(
    *,
    candidate_id: str = "",
    geometry_hash: str = "",
    account_id: str = "",
) -> str:
    return "|".join((account_id, candidate_id, geometry_hash))


def is_stale_decision(
    *,
    decided_at: datetime | None,
    now: datetime,
    ttl_seconds: int = 60,
) -> DecisionFreshness:
    if decided_at is None:
        return DecisionFreshness(
            stale=True,
            age_seconds=float("inf"),
            ttl_seconds=ttl_seconds,
            reason="missing_decision_timestamp",
        )
    if decided_at.tzinfo is None or now.tzinfo is None:
        return DecisionFreshness(
            stale=True,
            age_seconds=float("inf"),
            ttl_seconds=ttl_seconds,
            reason="naive_timestamp",
        )
    age = (now - decided_at).total_seconds()
    if age < 0:
        return DecisionFreshness(
            stale=True,
            age_seconds=age,
            ttl_seconds=ttl_seconds,
            reason="decision_in_future",
        )
    if age > ttl_seconds:
        return DecisionFreshness(
            stale=True,
            age_seconds=age,
            ttl_seconds=ttl_seconds,
            reason="stale_decision",
        )
    return DecisionFreshness(
        stale=False,
        age_seconds=age,
        ttl_seconds=ttl_seconds,
        reason="fresh",
    )


@dataclass
class DuplicateGuard:
    """Tracks recent order/decision signatures to prevent duplicates."""

    ttl: timedelta = field(default_factory=lambda: timedelta(minutes=15))
    _seen: dict[str, datetime] = field(default_factory=dict)

    def observe(self, signature: str, at: datetime) -> None:
        self._purge(at)
        if signature:
            self._seen[signature] = at

    def is_duplicate(
        self,
        *,
        candidate_id: str = "",
        geometry_hash: str = "",
        account_id: str = "",
        open_candidate_ids: tuple[str, ...] = (),
        open_geometry_hashes: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        now = now or datetime.now(tz=UTC)
        self._purge(now)
        if candidate_id and candidate_id in open_candidate_ids:
            return True, "equivalent_position_candidate"
        if geometry_hash and geometry_hash in open_geometry_hashes:
            return True, "equivalent_position_geometry"
        sig = decision_signature(
            candidate_id=candidate_id,
            geometry_hash=geometry_hash,
            account_id=account_id,
        )
        if sig and sig in self._seen:
            return True, "duplicate_order"
        return False, ""

    def remember_decision(self, candidate: Any, *, account_id: str, at: datetime) -> None:
        sig = decision_signature(
            candidate_id=str(getattr(candidate, "candidate_id", "") or ""),
            geometry_hash=str(getattr(candidate, "geometry_hash", "") or ""),
            account_id=account_id,
        )
        self.observe(sig, at)

    def _purge(self, now: datetime) -> None:
        cutoff = now - self.ttl
        stale = [k for k, ts in self._seen.items() if ts < cutoff]
        for key in stale:
            del self._seen[key]
