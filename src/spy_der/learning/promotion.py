"""Human-gated promotion. The Dojo never writes champion.json."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spy_der.util.files import atomic_write_json

__all__ = [
    "CHALLENGERS_DIRNAME",
    "CHAMPION_FILENAME",
    "PENDING_DIRNAME",
    "PromotionError",
    "StagedCandidate",
    "list_pending",
    "promote_pending",
    "reject_pending",
    "stage_pending_review",
]

PENDING_DIRNAME = "pending_review"
CHALLENGERS_DIRNAME = "challengers"
CHAMPION_FILENAME = "champion.json"


class PromotionError(RuntimeError):
    """Raised when a promotion workflow invariant is violated."""


@dataclass(frozen=True, slots=True)
class StagedCandidate:
    candidate_id: str
    path: Path
    payload: dict[str, Any]


def _pending_dir(configs_dir: str | Path) -> Path:
    path = Path(configs_dir) / PENDING_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _challengers_dir(configs_dir: str | Path) -> Path:
    path = Path(configs_dir) / CHALLENGERS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def stage_pending_review(
    configs_dir: str | Path,
    *,
    candidate_id: str,
    payload: dict[str, Any],
) -> Path:
    """Stage a challenger under pending_review/. Never touches champion.json."""
    if not candidate_id:
        raise PromotionError("candidate_id required")
    target = _pending_dir(configs_dir) / f"{candidate_id}.json"
    body = {
        "candidate_id": candidate_id,
        "status": "pending_review",
        "auto_promote": False,
        **payload,
    }
    atomic_write_json(target, body)
    # Also keep a copy under challengers/ for audit.
    challenger = _challengers_dir(configs_dir) / f"{candidate_id}.json"
    atomic_write_json(challenger, body)
    return target


def list_pending(configs_dir: str | Path) -> list[StagedCandidate]:
    root = Path(configs_dir) / PENDING_DIRNAME
    if not root.is_dir():
        return []
    out: list[StagedCandidate] = []
    for path in sorted(root.glob("*.json")):
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            continue
        out.append(
            StagedCandidate(
                candidate_id=str(payload.get("candidate_id") or path.stem),
                path=path,
                payload=payload,
            )
        )
    return out


def promote_pending(
    configs_dir: str | Path,
    candidate_id: str,
    *,
    human_ack: str,
) -> Path:
    """Promote a pending candidate to champion.json — requires explicit ack."""
    if human_ack.strip().upper() != "PROMOTE":
        raise PromotionError("human_ack must be exactly 'PROMOTE'")
    pending = Path(configs_dir) / PENDING_DIRNAME / f"{candidate_id}.json"
    if not pending.is_file():
        raise PromotionError(f"no pending candidate {candidate_id!r}")
    with open(pending, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise PromotionError("pending payload malformed")
    champion = Path(configs_dir) / CHAMPION_FILENAME
    body = {
        **payload,
        "status": "champion",
        "promoted_with_ack": human_ack,
    }
    atomic_write_json(champion, body)
    pending.unlink(missing_ok=True)
    return champion


def reject_pending(configs_dir: str | Path, candidate_id: str) -> None:
    pending = Path(configs_dir) / PENDING_DIRNAME / f"{candidate_id}.json"
    if pending.is_file():
        rejected = Path(configs_dir) / CHALLENGERS_DIRNAME / f"rejected_{candidate_id}.json"
        rejected.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(pending), str(rejected))
