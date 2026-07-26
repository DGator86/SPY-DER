"""Promotion to champion.json — by validated Dojo trial, or by human ack.

Two doors lead to ``champion.json`` and both are locked:

* :func:`promote_pending` needs an operator to say ``PROMOTE``.
* :func:`auto_promote_pending` needs a promotion trial report that says
  ``validated`` — the Dojo re-ran the system with the recommended change and
  every gate passed (see :mod:`spy_der.learning.promotion_trial`).

Neither door opens on a recommendation alone. Every promotion snapshots the
config it replaced into ``champion_history/`` so
:func:`rollback_champion` can put the previous one back without a rebuild.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spy_der.util.files import atomic_write_json

__all__ = [
    "CHALLENGERS_DIRNAME",
    "CHAMPION_FILENAME",
    "HISTORY_DIRNAME",
    "PENDING_DIRNAME",
    "PROMOTED_DIRNAME",
    "PromotionError",
    "StagedCandidate",
    "auto_promote_pending",
    "current_champion",
    "list_pending",
    "promote_pending",
    "reject_pending",
    "rollback_champion",
    "stage_pending_review",
]

PENDING_DIRNAME = "pending_review"
CHALLENGERS_DIRNAME = "challengers"
PROMOTED_DIRNAME = "promoted"
HISTORY_DIRNAME = "champion_history"
CHAMPION_FILENAME = "champion.json"


class PromotionError(RuntimeError):
    """Raised when a promotion workflow invariant is violated."""


@dataclass(frozen=True, slots=True)
class StagedCandidate:
    candidate_id: str
    path: Path
    payload: dict[str, Any]


def _subdir(configs_dir: str | Path, name: str) -> Path:
    path = Path(configs_dir) / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pending_dir(configs_dir: str | Path) -> Path:
    return _subdir(configs_dir, PENDING_DIRNAME)


def _challengers_dir(configs_dir: str | Path) -> Path:
    return _subdir(configs_dir, CHALLENGERS_DIRNAME)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def stage_pending_review(
    configs_dir: str | Path,
    *,
    candidate_id: str,
    payload: dict[str, Any],
    auto_promote: bool = False,
) -> Path:
    """Stage a challenger under pending_review/. Never touches champion.json."""
    if not candidate_id:
        raise PromotionError("candidate_id required")
    target = _pending_dir(configs_dir) / f"{candidate_id}.json"
    body = {
        "candidate_id": candidate_id,
        "status": "pending_review",
        "auto_promote": bool(auto_promote),
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
        payload = _read_json(path)
        if payload is None:
            continue
        out.append(
            StagedCandidate(
                candidate_id=str(payload.get("candidate_id") or path.stem),
                path=path,
                payload=payload,
            )
        )
    return out


def current_champion(configs_dir: str | Path) -> dict[str, Any] | None:
    """The promoted config, or None when nothing has been promoted yet."""
    return _read_json(Path(configs_dir) / CHAMPION_FILENAME)


def _archive_champion(configs_dir: str | Path) -> Path | None:
    """Snapshot the outgoing champion so a promotion is always reversible."""
    champion = Path(configs_dir) / CHAMPION_FILENAME
    payload = _read_json(champion)
    if payload is None:
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    history = _subdir(configs_dir, HISTORY_DIRNAME)
    archived = history / f"champion-{stamp}.json"
    # Second-granularity stamps collide when a promotion is rolled back at once.
    suffix = 1
    while archived.exists():
        archived = history / f"champion-{stamp}-{suffix}.json"
        suffix += 1
    atomic_write_json(archived, {**payload, "archived_at": datetime.now(UTC).isoformat()})
    return archived


def _write_champion(
    configs_dir: str | Path,
    *,
    payload: dict[str, Any],
    extra: dict[str, Any],
) -> Path:
    previous = _archive_champion(configs_dir)
    champion = Path(configs_dir) / CHAMPION_FILENAME
    body = {
        **payload,
        "status": "champion",
        "promoted_at": datetime.now(UTC).isoformat(),
        "previous_champion": str(previous) if previous else None,
        **extra,
    }
    atomic_write_json(champion, body)
    return champion


def _take_pending(configs_dir: str | Path, candidate_id: str) -> dict[str, Any]:
    pending = Path(configs_dir) / PENDING_DIRNAME / f"{candidate_id}.json"
    if not pending.is_file():
        raise PromotionError(f"no pending candidate {candidate_id!r}")
    payload = _read_json(pending)
    if payload is None:
        raise PromotionError("pending payload malformed")
    return payload


def _retire_pending(configs_dir: str | Path, candidate_id: str) -> None:
    """Move the staged file out of pending_review/ once it has been promoted."""
    pending = Path(configs_dir) / PENDING_DIRNAME / f"{candidate_id}.json"
    if not pending.is_file():
        return
    promoted = _subdir(configs_dir, PROMOTED_DIRNAME) / f"{candidate_id}.json"
    shutil.move(str(pending), str(promoted))


def promote_pending(
    configs_dir: str | Path,
    candidate_id: str,
    *,
    human_ack: str,
) -> Path:
    """Promote a pending candidate to champion.json — requires explicit ack."""
    if human_ack.strip().upper() != "PROMOTE":
        raise PromotionError("human_ack must be exactly 'PROMOTE'")
    payload = _take_pending(configs_dir, candidate_id)
    champion = _write_champion(
        configs_dir,
        payload=payload,
        extra={"promoted_with_ack": human_ack, "promoted_by": "human"},
    )
    _retire_pending(configs_dir, candidate_id)
    return champion


def auto_promote_pending(
    configs_dir: str | Path,
    candidate_id: str,
    *,
    validation: dict[str, Any],
    knobs: dict[str, Any] | None = None,
) -> Path:
    """Promote automatically on the strength of a validated promotion trial.

    ``validation`` is the report from
    :func:`spy_der.learning.promotion_trial.run_promotion_trial`. It must say
    ``status == "validated"`` and carry the gates that were checked — an
    unvalidated or gate-less report is refused, so a caller cannot fabricate a
    promotion by passing an empty dict.
    """
    if not isinstance(validation, dict):
        raise PromotionError("validation report required")
    if validation.get("status") != "validated":
        raise PromotionError(
            f"promotion trial not validated (status={validation.get('status')!r})"
        )
    gates = validation.get("gates")
    if not isinstance(gates, list) or not gates:
        raise PromotionError("validation report carries no gates")
    failed = [g for g in gates if isinstance(g, dict) and not g.get("passed")]
    if failed:
        raise PromotionError(
            "validation report has failing gates: "
            + ", ".join(str(g.get("name")) for g in failed)
        )

    payload = _take_pending(configs_dir, candidate_id)
    champion = _write_champion(
        configs_dir,
        payload=payload,
        extra={
            "promoted_by": "dojo_auto",
            "validation": validation,
            "knobs": dict(knobs) if knobs else validation.get("knobs") or {},
        },
    )
    _retire_pending(configs_dir, candidate_id)
    return champion


def rollback_champion(configs_dir: str | Path) -> Path | None:
    """Restore the most recently archived champion. Returns its path, or None."""
    history = Path(configs_dir) / HISTORY_DIRNAME
    if not history.is_dir():
        return None
    snapshots = sorted(history.glob("champion-*.json"))
    if not snapshots:
        return None
    payload = _read_json(snapshots[-1])
    if payload is None:
        return None
    restored = {
        k: v for k, v in payload.items() if k not in {"archived_at", "previous_champion"}
    }
    champion = _write_champion(
        configs_dir,
        payload=restored,
        extra={"promoted_by": "rollback", "rolled_back_from": str(snapshots[-1])},
    )
    snapshots[-1].unlink(missing_ok=True)
    return champion


def reject_pending(configs_dir: str | Path, candidate_id: str) -> None:
    pending = Path(configs_dir) / PENDING_DIRNAME / f"{candidate_id}.json"
    if pending.is_file():
        rejected = Path(configs_dir) / CHALLENGERS_DIRNAME / f"rejected_{candidate_id}.json"
        rejected.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(pending), str(rejected))
