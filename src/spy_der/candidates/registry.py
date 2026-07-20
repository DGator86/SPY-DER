"""Approved candidate-family registry (master spec §7.1, §31)."""

from __future__ import annotations

from spy_der.contracts.candidates import CandidateDirection, CandidateFamily, DebitCredit

__all__ = [
    "APPROVED_FAMILIES",
    "REJECTED_FAMILIES",
    "FamilySpec",
    "canonical_family_name",
    "family_direction",
    "family_entry_bias",
    "is_approved_family",
]


class FamilySpec:
    __slots__ = ("aliases", "direction", "entry_bias", "family")

    def __init__(
        self,
        family: CandidateFamily,
        direction: CandidateDirection,
        entry_bias: DebitCredit,
        aliases: tuple[str, ...] = (),
    ) -> None:
        self.family = family
        self.direction = direction
        self.entry_bias = entry_bias
        self.aliases = aliases


_SPECS: tuple[FamilySpec, ...] = (
    FamilySpec(CandidateFamily.LONG_CALL, CandidateDirection.BULLISH, DebitCredit.DEBIT),
    FamilySpec(CandidateFamily.LONG_PUT, CandidateDirection.BEARISH, DebitCredit.DEBIT),
    FamilySpec(
        CandidateFamily.CALL_DEBIT_SPREAD,
        CandidateDirection.BULLISH,
        DebitCredit.DEBIT,
        aliases=("long_call_spread",),
    ),
    FamilySpec(
        CandidateFamily.PUT_DEBIT_SPREAD,
        CandidateDirection.BEARISH,
        DebitCredit.DEBIT,
        aliases=("long_put_spread",),
    ),
    FamilySpec(
        CandidateFamily.BULL_PUT_CREDIT_SPREAD,
        CandidateDirection.BULLISH,
        DebitCredit.CREDIT,
        aliases=("put_credit",),
    ),
    FamilySpec(
        CandidateFamily.BEAR_CALL_CREDIT_SPREAD,
        CandidateDirection.BEARISH,
        DebitCredit.CREDIT,
        aliases=("call_credit",),
    ),
    FamilySpec(
        CandidateFamily.IRON_CONDOR,
        CandidateDirection.SHORT_VOL,
        DebitCredit.CREDIT,
    ),
    FamilySpec(
        CandidateFamily.IRON_BUTTERFLY,
        CandidateDirection.SHORT_VOL,
        DebitCredit.CREDIT,
        aliases=("iron_fly",),
    ),
    FamilySpec(
        CandidateFamily.BOUNDED_BROKEN_WING_BUTTERFLY,
        CandidateDirection.NEUTRAL,
        DebitCredit.CREDIT,
        aliases=("broken_wing",),
    ),
    FamilySpec(
        CandidateFamily.LONG_STRADDLE,
        CandidateDirection.LONG_VOL,
        DebitCredit.DEBIT,
    ),
    FamilySpec(
        CandidateFamily.LONG_STRANGLE,
        CandidateDirection.LONG_VOL,
        DebitCredit.DEBIT,
    ),
    FamilySpec(
        CandidateFamily.BOUNDED_BACKSPREAD_CALL,
        CandidateDirection.BULLISH,
        DebitCredit.DEBIT,
        aliases=("backspread_call",),
    ),
    FamilySpec(
        CandidateFamily.BOUNDED_BACKSPREAD_PUT,
        CandidateDirection.BEARISH,
        DebitCredit.DEBIT,
        aliases=("backspread_put",),
    ),
)

APPROVED_FAMILIES: frozenset[str] = frozenset(spec.family.value for spec in _SPECS)

REJECTED_FAMILIES: frozenset[str] = frozenset(
    {
        "naked_short_call",
        "naked_short_put",
        "naked_defended_call",
        "cash_secured_put",
        "covered_call",
        "unbounded_ratio",
        "unbounded_backspread",
    }
)

_BY_NAME: dict[str, FamilySpec] = {}
for _spec in _SPECS:
    _BY_NAME[_spec.family.value] = _spec
    for _alias in _spec.aliases:
        _BY_NAME[_alias] = _spec


def is_approved_family(family: str) -> bool:
    return family in _BY_NAME and family not in REJECTED_FAMILIES


def family_direction(family: str) -> CandidateDirection:
    return _BY_NAME[family].direction


def family_entry_bias(family: str) -> DebitCredit:
    return _BY_NAME[family].entry_bias


def canonical_family_name(family: str) -> str:
    return _BY_NAME[family].family.value
