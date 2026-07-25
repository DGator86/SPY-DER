"""Execution package: guard, order state machine, simulator, accounts, fills.

:mod:`spy_der.execution.guard` is the deterministic gate every executor sits
behind — an AI decision is advisory until the guard approves it.
"""

from spy_der.execution.accounts import IsolatedAccountBook, default_account_ids
from spy_der.execution.fill_records import (
    ALLOWED_MODES,
    ALLOWED_SOURCES,
    enrich_fill_fractions,
    fill_fraction,
    validate_fill_record,
)
from spy_der.execution.guard import (
    ExecutionMode,
    GuardBlock,
    GuardContext,
    GuardDecision,
    GuardLimits,
    apply_execution_guard,
    guard_shadow_decision,
)
from spy_der.execution.reconciliation import reconcile_account
from spy_der.execution.simulator import PaperExecutionSimulator, SimulatorConfig
from spy_der.execution.state_machine import is_terminal_order, validate_order_transition

__all__ = [
    "ALLOWED_MODES",
    "ALLOWED_SOURCES",
    "ExecutionMode",
    "GuardBlock",
    "GuardContext",
    "GuardDecision",
    "GuardLimits",
    "IsolatedAccountBook",
    "PaperExecutionSimulator",
    "SimulatorConfig",
    "apply_execution_guard",
    "default_account_ids",
    "enrich_fill_fractions",
    "fill_fraction",
    "guard_shadow_decision",
    "is_terminal_order",
    "reconcile_account",
    "validate_fill_record",
    "validate_order_transition",
]
