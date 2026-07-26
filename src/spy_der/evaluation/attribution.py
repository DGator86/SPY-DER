"""Shadow account — separates model quality from execution quality.

A losing session has at least five distinct causes and they demand opposite
responses. If the forecast was wrong, retrain. If the approved trade was fine
but a worse candidate got taken, fix selection. If the structure and size were
right but the fills were bad, fix execution. If the plan was abandoned early,
that is behaviour, not modelling. Aggregating all of it into one P&L number
makes every one of those invisible.

This module runs the approved decision and what actually happened side by side
and decomposes the difference into a waterfall that reconciles exactly:

    model_pnl  +  participation + selection + sizing + entry + exit  =  actual_pnl

Each stage changes exactly one attribute of the trade and is priced at the
stage before it, so the components sum to the gap by construction — there is no
residual bucket to hide a mistake in. `assert_reconciles` is a public check
because a decomposition that does not add up is worse than none at all.

Price convention — read this before supplying prices. Every price here is the
**signed position value per share, from the holder's perspective**: the mark of
the position as an asset, not the cash that changed hands. Long structures carry
their market value; short structures carry its negation, at entry *and at exit*.
P&L is then always ``(exit_price - entry_price) * contracts * multiplier``, with
no per-structure direction sign.

Worked both ways, because getting the exit sign wrong is the easy mistake:

===========================  ===========  ==========  ===========
position                     entry_price  exit_price  P&L / share
===========================  ===========  ==========  ===========
debit spread paid 1.00,       ``1.00``     ``1.50``    ``+0.50``
 closed at 1.50
credit spread sold 0.50,     ``-0.50``    ``0``       ``+0.50``
 expires worthless
credit spread sold 0.50,     ``-0.50``    ``-0.20``   ``+0.30``
 bought back at 0.20
credit spread sold 0.50,     ``-0.50``    ``-1.00``   ``-0.50``
 bought back at 1.00
===========================  ===========  ==========  ===========

Note the third and fourth rows: buying a short structure back at 0.20 is
``exit_price=-0.20``, **not** ``+0.20``. A short position's value stays negative
for its whole life. This is the same sign convention `evaluation.settlement`
uses for `entry_credit`.

Nothing here feeds the decision path. It reads settled history and produces a
report; it cannot size, veto, or approve anything.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

__all__ = [
    "ATTRIBUTION_SCHEMA",
    "ActualTrade",
    "AttributionComponent",
    "AttributionConfig",
    "BehaviorFlag",
    "PlannedTrade",
    "ShadowAccountReport",
    "TradeAttribution",
    "assert_reconciles",
    "attribute_session",
    "attribute_trade",
]

ATTRIBUTION_SCHEMA = "attribution.v1"

#: Options contract multiplier. Configurable so the same waterfall can price a
#: non-standard deliverable without a second implementation.
DEFAULT_MULTIPLIER = Decimal("100")

#: Money is quantized once, at the end of each stage, to the same precision
#: `evaluation.settlement.settle_candidate` uses. Quantizing per stage rather
#: than only at the end is what keeps the components summing to the gap exactly.
_CENTS = Decimal("0.0001")


class AttributionComponent(StrEnum):
    """Stages of the waterfall, in the order they are applied."""

    PARTICIPATION = "participation"
    SELECTION = "selection"
    SIZING = "sizing"
    ENTRY = "entry"
    EXIT = "exit"


class BehaviorFlag(StrEnum):
    """Named deviations from the approved plan.

    Flags describe *what* diverged. They carry no severity and no P&L — the
    waterfall already says what each divergence cost, and a flag that is
    expensive in one session may be free in another.
    """

    MISSED_SIGNAL = "missed_signal"
    UNAPPROVED_TRADE = "unapproved_trade"
    OVERSIZED = "oversized"
    UNDERSIZED = "undersized"
    LATE_ENTRY = "late_entry"
    PREMATURE_EXIT = "premature_exit"
    HELD_PAST_PLAN = "held_past_plan"
    OVERTRADING = "overtrading"
    REVENGE_TRADE = "revenge_trade"


@dataclass(frozen=True, slots=True)
class AttributionConfig:
    """Thresholds separating noise from a real deviation."""

    multiplier: Decimal = DEFAULT_MULTIPLIER
    #: Contract-count deviation below this fraction is rounding, not sizing.
    size_tolerance: float = 0.10
    #: Seconds between the decision and the actual entry before it is late.
    late_entry_seconds: float = 120.0
    #: Unapproved trades in a session before the session is overtrading.
    max_unapproved_per_session: int = 1
    #: Taken/approved ratio above which the session is overtrading.
    overtrade_ratio: float = 1.5
    #: An unapproved trade opened within this many seconds of a losing exit.
    revenge_window_seconds: float = 900.0


@dataclass(frozen=True, slots=True)
class PlannedTrade:
    """What SPY-DER's decision authority approved for one snapshot.

    `entry_price` and `exit_price` are the model's own fills — the economics
    service's modelled entry and the planned exit. This is the trade the
    shadow account holds; it is never adjusted to match reality.
    """

    candidate_id: str
    contracts: int
    entry_price: Decimal
    exit_price: Decimal
    snapshot_id: str = ""
    session_date: str = ""
    decided_at: datetime | None = None
    planned_exit_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ActualTrade:
    """What actually filled.

    `modeled_entry_price` / `modeled_exit_price` are the model's fills **for
    this candidate**, which is what makes selection separable from execution:
    without them a substituted candidate's entire difference is indistinguishable
    from a bad fill. When the taken candidate is the approved one they default to
    the plan's own prices. When a different candidate was taken and no modelled
    prices are supplied, the whole difference lands in `selection` and the
    attribution says so in `notes` rather than guessing.
    """

    candidate_id: str
    contracts: int
    entry_price: Decimal
    exit_price: Decimal
    approved: bool = True
    modeled_entry_price: Decimal | None = None
    modeled_exit_price: Decimal | None = None
    entry_at: datetime | None = None
    exit_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TradeAttribution:
    """One snapshot's model-vs-actual decomposition."""

    schema_version: str = ATTRIBUTION_SCHEMA
    session_date: str = ""
    snapshot_id: str = ""
    planned_candidate_id: str | None = None
    actual_candidate_id: str | None = None
    model_pnl: Decimal = Decimal("0")
    actual_pnl: Decimal = Decimal("0")
    gap: Decimal = Decimal("0")
    components: Mapping[str, Decimal] = field(default_factory=dict)
    flags: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "session_date": self.session_date,
            "snapshot_id": self.snapshot_id,
            "planned_candidate_id": self.planned_candidate_id,
            "actual_candidate_id": self.actual_candidate_id,
            "model_pnl": str(self.model_pnl),
            "actual_pnl": str(self.actual_pnl),
            "gap": str(self.gap),
            "components": {k: str(v) for k, v in self.components.items()},
            "flags": list(self.flags),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class ShadowAccountReport:
    """Model book and actual book, scored separately and against each other."""

    schema_version: str = ATTRIBUTION_SCHEMA
    sessions: tuple[str, ...] = ()
    n_planned: int = 0
    n_taken: int = 0
    n_missed: int = 0
    n_unapproved: int = 0
    model_pnl: Decimal = Decimal("0")
    actual_pnl: Decimal = Decimal("0")
    gap: Decimal = Decimal("0")
    components: Mapping[str, Decimal] = field(default_factory=dict)
    model_win_rate: float | None = None
    actual_win_rate: float | None = None
    model_expectancy: Decimal = Decimal("0")
    actual_expectancy: Decimal = Decimal("0")
    flag_counts: Mapping[str, int] = field(default_factory=dict)
    verdict: str = "no_data"
    trades: tuple[TradeAttribution, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "sessions": list(self.sessions),
            "n_planned": self.n_planned,
            "n_taken": self.n_taken,
            "n_missed": self.n_missed,
            "n_unapproved": self.n_unapproved,
            "model_pnl": str(self.model_pnl),
            "actual_pnl": str(self.actual_pnl),
            "gap": str(self.gap),
            "components": {k: str(v) for k, v in self.components.items()},
            "model_win_rate": self.model_win_rate,
            "actual_win_rate": self.actual_win_rate,
            "model_expectancy": str(self.model_expectancy),
            "actual_expectancy": str(self.actual_expectancy),
            "flag_counts": dict(self.flag_counts),
            "verdict": self.verdict,
            "trades": [t.to_dict() for t in self.trades],
        }


def _pnl(
    entry: Decimal, exit_: Decimal, contracts: int, multiplier: Decimal
) -> Decimal:
    """Signed structure P&L. See the module docstring on price convention."""
    return ((exit_ - entry) * Decimal(contracts) * multiplier).quantize(_CENTS)


def _elapsed(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def attribute_trade(
    planned: PlannedTrade | None,
    actual: ActualTrade | None,
    *,
    config: AttributionConfig | None = None,
) -> TradeAttribution:
    """Decompose one snapshot into the waterfall.

    Either side may be absent: no `actual` is a missed signal, no `planned` is
    an unapproved trade. Both absent is a programming error, not a data case.
    """
    cfg = config or AttributionConfig()
    if planned is None and actual is None:
        raise ValueError("attribute_trade requires a planned or an actual trade")

    flags: list[str] = []
    notes: list[str] = []
    components = {c.value: Decimal("0") for c in AttributionComponent}

    model_pnl = (
        _pnl(planned.entry_price, planned.exit_price, planned.contracts, cfg.multiplier)
        if planned is not None
        else Decimal("0")
    )

    # Not taken: the whole model position is forgone. The sign is not assumed —
    # skipping a losing signal is a positive contribution, and the report should
    # say so rather than scoring every deviation as damage.
    if actual is None:
        assert planned is not None
        components[AttributionComponent.PARTICIPATION.value] = -model_pnl
        flags.append(BehaviorFlag.MISSED_SIGNAL.value)
        return TradeAttribution(
            session_date=planned.session_date,
            snapshot_id=planned.snapshot_id,
            planned_candidate_id=planned.candidate_id,
            actual_candidate_id=None,
            model_pnl=model_pnl,
            actual_pnl=Decimal("0"),
            gap=-model_pnl,
            components=components,
            flags=tuple(flags),
            notes=("approved trade was not taken",),
        )

    actual_pnl = _pnl(
        actual.entry_price, actual.exit_price, actual.contracts, cfg.multiplier
    )

    # Taken with nothing approved: there is no model position to compare
    # against, so the entire result is participation. Splitting it further would
    # imply an approved baseline that never existed.
    if planned is None:
        components[AttributionComponent.PARTICIPATION.value] = actual_pnl
        flags.append(BehaviorFlag.UNAPPROVED_TRADE.value)
        return TradeAttribution(
            session_date="",
            snapshot_id="",
            planned_candidate_id=None,
            actual_candidate_id=actual.candidate_id,
            model_pnl=Decimal("0"),
            actual_pnl=actual_pnl,
            gap=actual_pnl,
            components=components,
            flags=tuple(flags),
            notes=("trade taken with no approved candidate",),
        )

    if not actual.approved:
        flags.append(BehaviorFlag.UNAPPROVED_TRADE.value)

    same_candidate = actual.candidate_id == planned.candidate_id
    modeled_entry = actual.modeled_entry_price
    modeled_exit = actual.modeled_exit_price
    if modeled_entry is None:
        modeled_entry = planned.entry_price if same_candidate else actual.entry_price
    if modeled_exit is None:
        modeled_exit = planned.exit_price if same_candidate else actual.exit_price
    if not same_candidate and (
        actual.modeled_entry_price is None or actual.modeled_exit_price is None
    ):
        notes.append(
            "substituted candidate has no modelled fills; selection absorbs "
            "execution for this trade"
        )

    # Waterfall. Each line changes exactly one attribute of the trade and is
    # priced against the line above it.
    p0 = model_pnl
    p1 = _pnl(modeled_entry, modeled_exit, planned.contracts, cfg.multiplier)
    p2 = _pnl(modeled_entry, modeled_exit, actual.contracts, cfg.multiplier)
    p3 = _pnl(actual.entry_price, modeled_exit, actual.contracts, cfg.multiplier)
    p4 = actual_pnl

    components[AttributionComponent.SELECTION.value] = p1 - p0
    components[AttributionComponent.SIZING.value] = p2 - p1
    components[AttributionComponent.ENTRY.value] = p3 - p2
    components[AttributionComponent.EXIT.value] = p4 - p3

    if not same_candidate:
        notes.append(
            f"took {actual.candidate_id} instead of approved {planned.candidate_id}"
        )

    if planned.contracts > 0:
        drift = (actual.contracts - planned.contracts) / planned.contracts
        if drift > cfg.size_tolerance:
            flags.append(BehaviorFlag.OVERSIZED.value)
        elif drift < -cfg.size_tolerance:
            flags.append(BehaviorFlag.UNDERSIZED.value)

    latency = _elapsed(planned.decided_at, actual.entry_at)
    if latency is not None and latency > cfg.late_entry_seconds:
        flags.append(BehaviorFlag.LATE_ENTRY.value)
        notes.append(f"entered {latency:.0f}s after the decision")

    exit_drift = _elapsed(planned.planned_exit_at, actual.exit_at)
    if exit_drift is not None:
        if exit_drift < 0:
            flags.append(BehaviorFlag.PREMATURE_EXIT.value)
        elif exit_drift > 0:
            flags.append(BehaviorFlag.HELD_PAST_PLAN.value)

    return TradeAttribution(
        session_date=planned.session_date,
        snapshot_id=planned.snapshot_id,
        planned_candidate_id=planned.candidate_id,
        actual_candidate_id=actual.candidate_id,
        model_pnl=model_pnl,
        actual_pnl=actual_pnl,
        gap=actual_pnl - model_pnl,
        components=components,
        flags=tuple(flags),
        notes=tuple(notes),
    )


def assert_reconciles(attribution: TradeAttribution) -> None:
    """Raise if the components do not sum to the gap.

    Public because callers persisting an attribution should be able to prove it
    before writing it, and because the waterfall's whole value is that it
    reconciles. Quantization is exact at `_CENTS`, so this is equality, not a
    tolerance check.
    """
    total = sum(attribution.components.values(), Decimal("0"))
    if total != attribution.gap:
        raise ValueError(
            f"attribution does not reconcile: components sum to {total}, "
            f"gap is {attribution.gap} (snapshot {attribution.snapshot_id!r})"
        )


def _verdict(model_pnl: Decimal, gap: Decimal, n_taken: int) -> str:
    """Which side is costing money — the point of keeping two books."""
    if n_taken == 0:
        return "no_data"
    model_ok = model_pnl > 0
    execution_ok = gap >= 0
    if model_ok and execution_ok:
        return "healthy"
    if model_ok and not execution_ok:
        return "execution_drag"
    if not model_ok and execution_ok:
        return "model_weak"
    return "model_weak_and_execution_drag"


def attribute_session(
    pairs: Sequence[tuple[PlannedTrade | None, ActualTrade | None]],
    *,
    config: AttributionConfig | None = None,
) -> ShadowAccountReport:
    """Attribute every snapshot, then score the two books separately.

    `pairs` is the aligned join of approved decisions and actual fills, keyed
    upstream by `snapshot_id`. Alignment is the caller's job: this module does
    not guess which fill belongs to which decision.
    """
    cfg = config or AttributionConfig()
    if not pairs:
        return ShadowAccountReport()

    attributions: list[TradeAttribution] = []
    for planned, actual in pairs:
        attribution = attribute_trade(planned, actual, config=cfg)
        assert_reconciles(attribution)
        attributions.append(attribution)

    totals = {c.value: Decimal("0") for c in AttributionComponent}
    for attribution in attributions:
        for name, value in attribution.components.items():
            totals[name] += value

    planned_trades = [a for a in attributions if a.planned_candidate_id is not None]
    taken = [a for a in attributions if a.actual_candidate_id is not None]
    missed = [a for a in attributions if BehaviorFlag.MISSED_SIGNAL.value in a.flags]
    unapproved = [
        a for a in attributions if BehaviorFlag.UNAPPROVED_TRADE.value in a.flags
    ]

    model_pnl = sum((a.model_pnl for a in attributions), Decimal("0"))
    actual_pnl = sum((a.actual_pnl for a in attributions), Decimal("0"))

    # Per-trade flags are counted per occurrence; session-scope flags describe
    # the session as a whole and are recorded once. They deliberately do not get
    # pinned to an arbitrary trade — "overtrading" is not any single trade's
    # property, and attaching it to one would misattribute it on drill-down.
    flag_counts: dict[str, int] = {}
    for attribution in attributions:
        for flag in attribution.flags:
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
    for flag in _session_flags(pairs, attributions, cfg):
        flag_counts.setdefault(flag, 1)

    sessions = tuple(
        sorted({a.session_date for a in attributions if a.session_date})
    )

    return ShadowAccountReport(
        sessions=sessions,
        n_planned=len(planned_trades),
        n_taken=len(taken),
        n_missed=len(missed),
        n_unapproved=len(unapproved),
        model_pnl=model_pnl,
        actual_pnl=actual_pnl,
        gap=actual_pnl - model_pnl,
        components=totals,
        model_win_rate=_win_rate([a.model_pnl for a in planned_trades]),
        actual_win_rate=_win_rate([a.actual_pnl for a in taken]),
        model_expectancy=_mean([a.model_pnl for a in planned_trades]),
        actual_expectancy=_mean([a.actual_pnl for a in taken]),
        flag_counts=flag_counts,
        verdict=_verdict(model_pnl, actual_pnl - model_pnl, len(taken)),
        trades=tuple(attributions),
    )


def _session_flags(
    pairs: Sequence[tuple[PlannedTrade | None, ActualTrade | None]],
    attributions: Sequence[TradeAttribution],
    cfg: AttributionConfig,
) -> tuple[str, ...]:
    """Flags that only exist at session scope, not per trade."""
    flags: list[str] = []

    n_approved = sum(1 for planned, _ in pairs if planned is not None)
    n_taken = sum(1 for _, actual in pairs if actual is not None)
    n_unapproved = sum(
        1
        for a in attributions
        if BehaviorFlag.UNAPPROVED_TRADE.value in a.flags
    )
    if n_unapproved > cfg.max_unapproved_per_session:
        flags.append(BehaviorFlag.OVERTRADING.value)
    elif n_approved > 0 and n_taken > n_approved * cfg.overtrade_ratio:
        flags.append(BehaviorFlag.OVERTRADING.value)

    if _has_revenge_trade(pairs, cfg):
        flags.append(BehaviorFlag.REVENGE_TRADE.value)
    return tuple(flags)


def _has_revenge_trade(
    pairs: Sequence[tuple[PlannedTrade | None, ActualTrade | None]],
    cfg: AttributionConfig,
) -> bool:
    """An unapproved entry shortly after a losing exit.

    Deliberately narrow: an *approved* trade taken after a loss is the system
    working, not revenge. Only entries the decision authority never sanctioned
    count, and only inside the configured window.
    """
    losing_exits = [
        actual.exit_at
        for _planned, actual in pairs
        if actual is not None
        and actual.exit_at is not None
        and _pnl(actual.entry_price, actual.exit_price, actual.contracts, cfg.multiplier)
        < 0
    ]
    if not losing_exits:
        return False
    for planned, actual in pairs:
        if actual is None or actual.entry_at is None:
            continue
        if planned is not None and actual.approved:
            continue
        for exit_at in losing_exits:
            delta = (actual.entry_at - exit_at).total_seconds()
            if 0 <= delta <= cfg.revenge_window_seconds:
                return True
    return False


def _win_rate(values: Sequence[Decimal]) -> float | None:
    if not values:
        return None
    return sum(1 for v in values if v > 0) / len(values)


def _mean(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return (sum(values, Decimal("0")) / Decimal(len(values))).quantize(_CENTS)
