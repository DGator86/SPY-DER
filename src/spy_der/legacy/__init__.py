"""Legacy structural interpretation layer (master spec §23)."""

from __future__ import annotations

from spy_der.legacy.analyzer import LegacyAnalyzer, LegacyConfig
from spy_der.legacy.permissions import (
    LegacyPermissionConfig,
    evaluate_operational_vetoes,
)

__all__ = [
    "LegacyAnalyzer",
    "LegacyConfig",
    "LegacyPermissionConfig",
    "evaluate_operational_vetoes",
]
