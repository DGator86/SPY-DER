"""Operational permissions and hard vetoes (master spec §23).

Immutable operational restrictions derived from the canonical snapshot: stale
trading data, missing/invalid chain, catalyst lockout, session closed, entry
cutoff, and insufficient liquidity. These are non-bypassable and kept separate
from empirical structural hypotheses (spec §23, §7). Missing inputs veto; they
never silently pass (spec §7.5).
"""

from __future__ import annotations

from dataclasses import dataclass

from spy_der.contracts.legacy import HardVeto, VetoCategory, VetoCode
from spy_der.contracts.market import (
    CanonicalMarketSnapshot,
    FeedComponent,
    FeedStatus,
    SessionStatus,
)

__all__ = ["LegacyPermissionConfig", "evaluate_operational_vetoes"]

# Components that must be fresh to permit an entry (settlement is EOD-only).
_TRADING_COMPONENTS = (
    FeedComponent.SPOT,
    FeedComponent.BARS,
    FeedComponent.OPTION_CHAIN,
)
_FRESH = (FeedStatus.LIVE, FeedStatus.DELAYED, FeedStatus.FALLBACK)


@dataclass(frozen=True, slots=True)
class LegacyPermissionConfig:
    entry_lockout_minutes: int = 15
    min_strikes: int = 5


def evaluate_operational_vetoes(
    snapshot: CanonicalMarketSnapshot,
    config: LegacyPermissionConfig | None = None,
) -> tuple[HardVeto, ...]:
    """Immutable operational restrictions for ``snapshot`` (spec §23)."""
    cfg = config or LegacyPermissionConfig()
    vetoes: list[HardVeto] = []

    def op(code: VetoCode, reason: str) -> None:
        vetoes.append(HardVeto(code=code, category=VetoCategory.OPERATIONAL, reason=reason))

    if snapshot.session_status in (SessionStatus.CLOSED, SessionStatus.HOLIDAY):
        op(VetoCode.SESSION_CLOSED, f"session {snapshot.session_status.value}")

    statuses = {obs.component: obs.status for obs in snapshot.feed_observations}
    for component in _TRADING_COMPONENTS:
        status = statuses.get(component)
        if status is None or status not in _FRESH:
            code = (
                VetoCode.MISSING_CHAIN
                if component is FeedComponent.OPTION_CHAIN
                else VetoCode.STALE_DATA
            )
            label = status.value if status is not None else "absent"
            op(code, f"{component.value} feed {label}")

    if not snapshot.option_chain or snapshot.chain_coverage.contracts_total == 0:
        op(VetoCode.MISSING_CHAIN, "option chain empty")
    elif not (snapshot.chain_coverage.has_calls and snapshot.chain_coverage.has_puts):
        op(VetoCode.INVALID_SURFACE, "chain missing one side")
    elif snapshot.chain_coverage.strikes_total < cfg.min_strikes:
        op(
            VetoCode.INSUFFICIENT_LIQUIDITY,
            f"{snapshot.chain_coverage.strikes_total} strikes < {cfg.min_strikes}",
        )

    if snapshot.catalyst_state.lockout_active:
        op(VetoCode.CATALYST_LOCKOUT, snapshot.catalyst_state.reason or "catalyst")

    if snapshot.session_status is SessionStatus.PRE_OPEN:
        op(VetoCode.SESSION_CLOSED, "pre-open")
    elif (
        snapshot.minutes_to_close is not None
        and snapshot.minutes_to_close <= cfg.entry_lockout_minutes
    ):
        op(
            VetoCode.ENTRY_CUTOFF,
            f"{snapshot.minutes_to_close}m to close <= {cfg.entry_lockout_minutes}m",
        )

    return tuple(vetoes)
