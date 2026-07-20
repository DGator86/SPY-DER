"""Session, entry-cutoff, catalyst, and emergency lockouts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from spy_der.contracts.risk import LockoutState, OperationalState

__all__ = [
    "ET",
    "LockoutConfig",
    "build_lockout_state",
    "build_operational_state",
    "is_entry_cutoff",
    "is_session_warmup",
]

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class LockoutConfig:
    morning_entry_time: time = time(10, 0)
    late_lockout_time: time = time(15, 30)
    require_market_open: bool = True


def is_session_warmup(now: datetime, cfg: LockoutConfig | None = None) -> bool:
    cfg = cfg or LockoutConfig()
    local = now.astimezone(ET)
    return local.timetz().replace(tzinfo=None) < cfg.morning_entry_time


def is_entry_cutoff(now: datetime, cfg: LockoutConfig | None = None) -> bool:
    cfg = cfg or LockoutConfig()
    local = now.astimezone(ET)
    return local.timetz().replace(tzinfo=None) >= cfg.late_lockout_time


def build_lockout_state(
    *,
    now: datetime,
    market_open: bool = True,
    catalyst_active: bool = False,
    emergency_lockout: bool = False,
    cfg: LockoutConfig | None = None,
) -> LockoutState:
    cfg = cfg or LockoutConfig()
    reasons: list[str] = []
    warmup = is_session_warmup(now, cfg)
    cutoff = is_entry_cutoff(now, cfg)
    if warmup:
        reasons.append("session_warmup")
    if cutoff:
        reasons.append("entry_cutoff")
    if catalyst_active:
        reasons.append("catalyst_lockout")
    if emergency_lockout:
        reasons.append("emergency_lockout")
    if cfg.require_market_open and not market_open:
        reasons.append("market_closed")
    return LockoutState(
        session_warmup=warmup,
        entry_cutoff=cutoff,
        emergency_lockout=emergency_lockout,
        catalyst_lockout=catalyst_active,
        reasons=tuple(reasons),
    )


def build_operational_state(
    *,
    now: datetime,
    market_open: bool = True,
    data_valid: bool = True,
    broker_available: bool = True,
    deployment_permission: bool = True,
    journal_available: bool = True,
    catalyst_active: bool = False,
    emergency_lockout: bool = False,
    extra_hard_vetoes: tuple[str, ...] = (),
    cfg: LockoutConfig | None = None,
) -> OperationalState:
    lockout = build_lockout_state(
        now=now,
        market_open=market_open,
        catalyst_active=catalyst_active,
        emergency_lockout=emergency_lockout,
        cfg=cfg,
    )
    hard = list(lockout.reasons)
    hard.extend(extra_hard_vetoes)
    return OperationalState(
        market_open=market_open,
        session_warmup=lockout.session_warmup,
        entry_locked=lockout.entry_cutoff or lockout.emergency_lockout,
        data_valid=data_valid,
        broker_available=broker_available,
        deployment_permission=deployment_permission,
        journal_available=journal_available,
        hard_vetoes=tuple(dict.fromkeys(hard)),
    )
