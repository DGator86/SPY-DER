"""Regime class constants (master spec §27 / System A prediction/regime_labels.py)."""

from __future__ import annotations

__all__ = ["REGIME_CLASSES", "REGIME_LABEL_VERSION"]

REGIME_CLASSES: tuple[str, ...] = (
    "long_gamma_pin",
    "short_gamma_trend",
    "flip_transition",
    "volatility_expansion",
)

REGIME_LABEL_VERSION = "v3.0.0"
