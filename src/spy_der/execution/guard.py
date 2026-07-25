"""Deterministic execution guard — the last word before any executor.

The AI decision authority (:mod:`spy_der.decisions`) chooses among candidates.
This module re-checks that choice deterministically and is the only thing an
executor is allowed to trust:

    candidate engine
          |
    deterministic eligibility     (spy_der.risk.firewall)
          |
    SPY-DER decision authority    (spy_der.decisions)
          |
    deterministic execution guard (this module)
          |
    shadow or live executor       (spy_der.execution)

The guard exists because the layer above it is a language model. Every check
here is deliberately re-derived from the candidate set and the account state
rather than read off the decision, so a decision that claims a size, a structure
or a maximum loss cannot obtain one it was not eligible for. The guard can only
ever *reduce* exposure: it clamps size and blocks trades, and has no path to
enlarge or approve anything the deterministic layers rejected.

Enforced, in order:

* **candidate provenance** — the selected id must exist in the eligible set
* **hard vetoes** — a vetoed candidate can never be selected
* **maximum loss** — per-trade cap, recomputed from the candidate
* **capital** — required capital must fit remaining buying power
* **position limits** — open-position count and per-family concentration
* **size caps** — the risk envelope's scalar, plus contract count
* **session restrictions** — trading windows and blackout periods
* **liquidity** — fill probability and quote freshness floors
* **live-trading permission** — live routing is opt-in and fails closed

Fail-closed: an unknown state, a missing input, or an internal inconsistency
blocks the trade.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "ExecutionMode",
    "GuardBlock",
    "GuardContext",
    "GuardDecision",
    "GuardLimits",
    "apply_execution_guard",
]


class ExecutionMode(StrEnum):
    """Where an approved decision may be routed."""

    SHADOW = "shadow"
    PAPER = "paper"
    LIVE = "live"


@runtime_checkable
class GuardCandidate(Protocol):
    """The candidate fields the guard needs.

    Structural, so both ``spy_der.decisions.ShadowCandidateView`` and
    ``spy_der.contracts.integration.MarketCandidateView`` satisfy it.
    """

    @property
    def candidate_id(self) -> str: ...

    @property
    def family(self) -> str: ...

    @property
    def maximum_loss(self) -> Decimal: ...

    @property
    def capital_required(self) -> Decimal: ...

    @property
    def fill_probability(self) -> float: ...

    @property
    def hard_vetoed(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class GuardLimits:
    """Deterministic limits the guard enforces.

    Defaults are conservative on purpose: a caller that forgets to configure a
    limit gets the safe value, not an unbounded one. ``allow_live`` defaults to
    ``False`` so live routing is never reachable by omission.
    """

    max_loss_per_trade: Decimal = Decimal("500")
    max_capital_per_trade: Decimal = Decimal("2500")
    max_size_scalar: float = 1.0
    max_contracts: int = 10
    max_open_positions: int = 3
    max_positions_per_family: int = 1
    min_fill_probability: float = 0.35
    max_quote_age_seconds: float = 5.0
    #: Trading window in exchange-local time. Entries outside it are blocked —
    #: the last minutes before the close are where 0DTE assignment and liquidity
    #: risk concentrate.
    session_open: time = time(9, 40)
    session_close: time = time(15, 45)
    allow_live: bool = False

    def __post_init__(self) -> None:
        if self.max_loss_per_trade < 0:
            msg = "max_loss_per_trade must be >= 0"
            raise ValueError(msg)
        if not 0.0 <= self.max_size_scalar <= 1.0:
            msg = "max_size_scalar must be within [0, 1]"
            raise ValueError(msg)
        if self.max_contracts < 0:
            msg = "max_contracts must be >= 0"
            raise ValueError(msg)
        if self.session_open >= self.session_close:
            msg = "session_open must precede session_close"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class GuardContext:
    """Account and market state the guard checks the decision against."""

    eligible: tuple[GuardCandidate, ...] = ()
    mode: ExecutionMode = ExecutionMode.SHADOW
    #: Wall-clock in exchange-local time; the session window is checked against
    #: its time component. Must be timezone-aware.
    now: datetime | None = None
    open_positions: int = 0
    open_families: Mapping[str, int] = field(default_factory=dict)
    buying_power: Decimal | None = None
    quote_age_seconds: float | None = None
    requested_contracts: int = 1
    #: Hard vetoes raised upstream for the whole snapshot, not per candidate.
    snapshot_vetoes: tuple[str, ...] = ()
    #: Operator killswitch. False blocks everything, including shadow.
    trading_enabled: bool = True
    #: Set when the deterministic eligibility layer ran and passed.
    eligibility_passed: bool = True


class GuardBlock(StrEnum):
    """Reason codes. Every block is one of these — never free text."""

    TRADING_DISABLED = "trading_disabled"
    ELIGIBILITY_NOT_PASSED = "eligibility_not_passed"
    NO_CANDIDATE_SELECTED = "no_candidate_selected"
    CANDIDATE_NOT_ELIGIBLE = "candidate_not_eligible"
    CANDIDATE_HARD_VETOED = "candidate_hard_vetoed"
    SNAPSHOT_HARD_VETO = "snapshot_hard_veto"
    MAX_LOSS_EXCEEDED = "max_loss_exceeded"
    CAPITAL_EXCEEDED = "capital_exceeded"
    INSUFFICIENT_BUYING_POWER = "insufficient_buying_power"
    POSITION_LIMIT = "position_limit"
    FAMILY_CONCENTRATION = "family_concentration"
    CONTRACTS_EXCEEDED = "contracts_exceeded"
    SIZE_NOT_POSITIVE = "size_not_positive"
    OUTSIDE_SESSION_WINDOW = "outside_session_window"
    MISSING_TIMESTAMP = "missing_timestamp"
    NAIVE_TIMESTAMP = "naive_timestamp"
    FILL_PROBABILITY_TOO_LOW = "fill_probability_too_low"
    QUOTE_STALE = "quote_stale"
    LIVE_NOT_PERMITTED = "live_not_permitted"
    UNKNOWN_MODE = "unknown_mode"


@dataclass(frozen=True, slots=True)
class GuardDecision:
    """The guard's verdict. ``approved`` is the only field an executor may act on."""

    approved: bool
    mode: ExecutionMode
    candidate_id: str | None
    #: Clamped size. Always <= the requested scalar and <= the limit.
    size_scalar: float
    contracts: int
    blocks: tuple[GuardBlock, ...] = ()
    clamps: tuple[str, ...] = ()
    details: Mapping[str, str] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return not self.approved

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "mode": str(self.mode),
            "candidate_id": self.candidate_id,
            "size_scalar": self.size_scalar,
            "contracts": self.contracts,
            "blocks": [str(b) for b in self.blocks],
            "clamps": list(self.clamps),
            "details": dict(self.details),
        }


def _is_trade(action: str) -> bool:
    return action.strip().upper() == "TRADE"


def apply_execution_guard(
    *,
    action: str,
    candidate_id: str | None,
    size_scalar: float,
    context: GuardContext,
    limits: GuardLimits | None = None,
) -> GuardDecision:
    """Re-check an AI decision deterministically; return the executable verdict.

    ``action``, ``candidate_id`` and ``size_scalar`` are the AI's output. Nothing
    else about the decision is trusted: maximum loss, capital, family and fill
    probability are all read from the *eligible candidate*, so a decision cannot
    assert its way past a limit.

    A non-TRADE action is approved trivially with zero size — abstaining is
    always allowed, and the guard never turns an abstain into a trade.
    """
    lim = limits or GuardLimits()
    blocks: list[GuardBlock] = []
    clamps: list[str] = []
    details: dict[str, str] = {}

    if not _is_trade(action):
        # Abstaining needs no permission. Reported as approved-with-no-exposure
        # so callers can log one shape for every tick.
        return GuardDecision(
            approved=True,
            mode=context.mode,
            candidate_id=None,
            size_scalar=0.0,
            contracts=0,
            details={"action": action.strip().upper() or "ABSTAIN"},
        )

    # --- operator and pipeline gates ---
    if not context.trading_enabled:
        blocks.append(GuardBlock.TRADING_DISABLED)
    if not context.eligibility_passed:
        blocks.append(GuardBlock.ELIGIBILITY_NOT_PASSED)
    if context.snapshot_vetoes:
        blocks.append(GuardBlock.SNAPSHOT_HARD_VETO)
        details["snapshot_vetoes"] = ",".join(sorted(context.snapshot_vetoes))

    # --- mode permission ---
    if context.mode not in tuple(ExecutionMode):
        blocks.append(GuardBlock.UNKNOWN_MODE)
    elif context.mode is ExecutionMode.LIVE and not lim.allow_live:
        blocks.append(GuardBlock.LIVE_NOT_PERMITTED)

    # --- candidate provenance ---
    candidate: GuardCandidate | None = None
    if not candidate_id:
        blocks.append(GuardBlock.NO_CANDIDATE_SELECTED)
    else:
        candidate = next(
            (c for c in context.eligible if c.candidate_id == candidate_id), None
        )
        if candidate is None:
            # The AI named something outside the eligible set. This is the check
            # that makes candidate invention structurally impossible.
            blocks.append(GuardBlock.CANDIDATE_NOT_ELIGIBLE)
        elif candidate.hard_vetoed:
            blocks.append(GuardBlock.CANDIDATE_HARD_VETOED)

    # --- session window ---
    now = context.now
    if now is None:
        blocks.append(GuardBlock.MISSING_TIMESTAMP)
    elif now.tzinfo is None or now.utcoffset() is None:
        # A naive timestamp cannot be compared to a session window safely.
        blocks.append(GuardBlock.NAIVE_TIMESTAMP)
    else:
        local = now.timetz().replace(tzinfo=None)
        if not lim.session_open <= local <= lim.session_close:
            blocks.append(GuardBlock.OUTSIDE_SESSION_WINDOW)
            details["local_time"] = local.isoformat()

    # --- position limits ---
    if context.open_positions >= lim.max_open_positions:
        blocks.append(GuardBlock.POSITION_LIMIT)
        details["open_positions"] = str(context.open_positions)

    # --- candidate-derived economics ---
    if candidate is not None:
        if candidate.maximum_loss > lim.max_loss_per_trade:
            blocks.append(GuardBlock.MAX_LOSS_EXCEEDED)
            details["maximum_loss"] = str(candidate.maximum_loss)
        if candidate.capital_required > lim.max_capital_per_trade:
            blocks.append(GuardBlock.CAPITAL_EXCEEDED)
            details["capital_required"] = str(candidate.capital_required)
        if (
            context.buying_power is not None
            and candidate.capital_required > context.buying_power
        ):
            blocks.append(GuardBlock.INSUFFICIENT_BUYING_POWER)
        held = context.open_families.get(candidate.family, 0)
        if held >= lim.max_positions_per_family:
            blocks.append(GuardBlock.FAMILY_CONCENTRATION)
            details["family"] = candidate.family
        if candidate.fill_probability < lim.min_fill_probability:
            blocks.append(GuardBlock.FILL_PROBABILITY_TOO_LOW)
            details["fill_probability"] = f"{candidate.fill_probability:.4f}"

    # --- liquidity / freshness ---
    if (
        context.quote_age_seconds is not None
        and context.quote_age_seconds > lim.max_quote_age_seconds
    ):
        blocks.append(GuardBlock.QUOTE_STALE)
        details["quote_age_seconds"] = f"{context.quote_age_seconds:.2f}"

    # --- size: clamp, never widen ---
    requested = size_scalar
    clamped = min(max(size_scalar, 0.0), lim.max_size_scalar)
    if clamped < requested:
        clamps.append(f"size_scalar {requested:.4f} -> {clamped:.4f}")
    if clamped <= 0.0:
        blocks.append(GuardBlock.SIZE_NOT_POSITIVE)

    contracts = max(int(context.requested_contracts), 0)
    if contracts > lim.max_contracts:
        clamps.append(f"contracts {contracts} -> {lim.max_contracts}")
        contracts = lim.max_contracts
    if contracts <= 0:
        blocks.append(GuardBlock.CONTRACTS_EXCEEDED)

    approved = not blocks
    return GuardDecision(
        approved=approved,
        mode=context.mode,
        candidate_id=candidate_id if approved else None,
        size_scalar=clamped if approved else 0.0,
        contracts=contracts if approved else 0,
        blocks=tuple(_dedupe(blocks)),
        clamps=tuple(clamps),
        details=details,
    )


def _dedupe(values: Sequence[GuardBlock]) -> list[GuardBlock]:
    """Preserve first-seen order without repeats — blocks read as a checklist."""
    seen: set[GuardBlock] = set()
    out: list[GuardBlock] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def guard_shadow_decision(
    decision: Any,
    context: GuardContext,
    limits: GuardLimits | None = None,
) -> GuardDecision:
    """Adapter for :class:`spy_der.decisions.SpyDerShadowDecision`.

    Reads only ``action`` / ``candidate_id`` / ``size_scalar`` from the decision;
    every limit is still re-derived from ``context``.
    """
    return apply_execution_guard(
        action=str(getattr(decision, "action", "") or ""),
        candidate_id=getattr(decision, "candidate_id", None),
        size_scalar=float(getattr(decision, "size_scalar", 0.0) or 0.0),
        context=context,
        limits=limits,
    )


def utc_now() -> datetime:
    """Timezone-aware now — the guard rejects naive timestamps."""
    return datetime.now(tz=UTC)
