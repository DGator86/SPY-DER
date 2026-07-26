"""Persist and reload archetype sampling weights across Dojo runs.

The universe phase computes an :class:`~spy_der.synthetic.evolution.EvolutionPlan`
after each generation. Without persistence, the next nightly Dojo run would
start equal-weighted again and the robustness gaps would never accumulate
training pressure. This module is the thin file bridge that keeps those
weights alive under ``configs/curriculum_weights.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from spy_der.synthetic.archetypes import ARCHETYPES
from spy_der.util.files import atomic_write_json

__all__ = [
    "CURRICULUM_WEIGHTS_FILENAME",
    "load_curriculum_weights",
    "save_curriculum_weights",
]

CURRICULUM_WEIGHTS_FILENAME = "curriculum_weights.json"


def _path(configs_dir: str | Path) -> Path:
    return Path(configs_dir) / CURRICULUM_WEIGHTS_FILENAME


def load_curriculum_weights(configs_dir: str | Path | None) -> dict[str, float] | None:
    """Return saved archetype weights, or ``None`` when nothing usable is stored."""
    if not configs_dir:
        return None
    path = _path(configs_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("weights")
    if not isinstance(raw, dict):
        return None
    weights: dict[str, float] = {}
    for archetype in ARCHETYPES:
        value = raw.get(archetype)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0.0:
            weights[archetype] = parsed
    if not weights:
        return None
    # Fill any missing archetype with the mean of known weights so a partial
    # file cannot silently starve a newly added archetype.
    mean = sum(weights.values()) / len(weights)
    for archetype in ARCHETYPES:
        weights.setdefault(archetype, mean)
    return weights


def save_curriculum_weights(
    configs_dir: str | Path | None,
    *,
    weights: dict[str, float],
    generation: int = 0,
    weak_archetypes: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path | None:
    """Atomically write the next-run sampling weights. Returns the path written.

    Persistence failures never abort a Dojo phase — sparring results still
    matter when the configs directory is read-only (tests, restricted mounts).
    """
    if not configs_dir:
        return None
    path = _path(configs_dir)
    body: dict[str, Any] = {
        "generation": int(generation),
        "weights": {a: float(weights[a]) for a in ARCHETYPES if a in weights},
        "weak_archetypes": list(weak_archetypes or []),
    }
    if extra:
        body.update(extra)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, body)
    except OSError:
        return None
    return path
