"""Live Dojo progress — what the dashboard shows while a run is working.

A Dojo oneshot can take over an hour and logs almost nothing until the end.
Without a progress file the tab looks idle even though CPU is pegged. This
module writes ``reports/dojo/progress.json`` and a ``dojo`` heartbeat on every
phase / session advance so `/v1/dojo/progress` can render a live square.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spy_der.runtime.heartbeat import write_heartbeat
from spy_der.util.files import atomic_write_json

__all__ = [
    "PHASES",
    "PROGRESS_FILENAME",
    "STALE_AFTER_SECONDS",
    "DojoProgress",
    "annotate_dojo_progress",
    "idle_dojo_progress",
    "read_dojo_progress",
]

PROGRESS_FILENAME = "progress.json"

#: Ordered exam stages the UI draws as a strip.
PHASES: tuple[str, ...] = (
    "recorded",
    "sequential",
    "learner",
    "universe",
    "promotion",
)

#: A running progress file older than this is treated as abandoned (oneshot
#: killed, disk full, etc.) so the dashboard square does not stay "WORKING".
STALE_AFTER_SECONDS = 120.0

_PHASE_LABELS = {
    "recorded": "Real tape",
    "sequential": "Blind days",
    "learner": "Adaptive change",
    "universe": "Synthetic sparring",
    "promotion": "Promotion trial",
}


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass
class DojoProgress:
    """Publisher for one Dojo process. Best-effort: never raises to the runner."""

    reports_dir: str | Path
    state_root: str | Path
    report_date: str
    started_at: datetime = field(default_factory=_now)
    _phases: dict[str, dict[str, Any]] = field(default_factory=dict)
    _phase: str = "starting"
    _detail: str = "Dojo starting"
    _status: str = "running"
    _report_path: str = ""

    def __post_init__(self) -> None:
        self.reports_dir = Path(self.reports_dir)
        self.state_root = Path(self.state_root)
        for name in PHASES:
            self._phases[name] = {
                "name": name,
                "label": _PHASE_LABELS[name],
                "status": "pending",
                "detail": "",
            }
        self._publish()

    def __enter__(self) -> DojoProgress:
        return self

    def __exit__(self, exc_type: Any, exc: Any, _tb: Any) -> None:
        if exc_type is not None and self._status == "running":
            self.fail(f"{getattr(exc_type, '__name__', 'Error')}: {exc}")
        return None

    @property
    def path(self) -> Path:
        return Path(self.reports_dir) / PROGRESS_FILENAME

    def begin_phase(self, phase: str, detail: str = "") -> None:
        if phase in self._phases:
            self._phases[phase]["status"] = "running"
            self._phases[phase]["detail"] = detail
        self._phase = phase
        self._detail = detail or f"Running {_PHASE_LABELS.get(phase, phase)}"
        self._status = "running"
        self._publish()

    def update(self, detail: str, *, phase: str | None = None) -> None:
        if phase is not None:
            self._phase = phase
            if phase in self._phases and self._phases[phase]["status"] == "pending":
                self._phases[phase]["status"] = "running"
        if self._phase in self._phases:
            self._phases[self._phase]["detail"] = detail
        self._detail = detail
        self._status = "running"
        self._publish()

    def finish_phase(self, phase: str, detail: str = "", *, skipped: bool = False) -> None:
        if phase in self._phases:
            self._phases[phase]["status"] = "skipped" if skipped else "done"
            self._phases[phase]["detail"] = detail
        self._detail = detail or (
            f"Skipped {_PHASE_LABELS.get(phase, phase)}"
            if skipped
            else f"Finished {_PHASE_LABELS.get(phase, phase)}"
        )
        self._publish()

    def finish(
        self,
        detail: str = "Dojo finished",
        *,
        summary: str | None = None,
        report_path: str | None = None,
    ) -> None:
        self._status = "finished"
        self._phase = "done"
        self._detail = summary or detail
        self._report_path = report_path or ""
        for _name, entry in self._phases.items():
            if entry["status"] == "running":
                entry["status"] = "done"
            elif entry["status"] == "pending":
                entry["status"] = "skipped"
                entry["detail"] = entry["detail"] or "not reached"
        self._publish(interval_seconds=0.0)

    def fail(self, detail: str) -> None:
        self._status = "failed"
        self._detail = detail
        if self._phase in self._phases:
            self._phases[self._phase]["status"] = "failed"
            self._phases[self._phase]["detail"] = detail
        self._publish(interval_seconds=0.0)

    def snapshot(self) -> dict[str, Any]:
        elapsed = max(0.0, time.time() - self.started_at.timestamp())
        payload: dict[str, Any] = {
            "status": self._status,
            "phase": self._phase,
            "phase_label": _PHASE_LABELS.get(self._phase, self._phase),
            "detail": self._detail,
            "report_date": self.report_date,
            "started_at": self.started_at.isoformat(),
            "updated_at": _now().isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            "pid": os.getpid(),
            "phases": [dict(self._phases[name]) for name in PHASES],
            "live": self._status == "running",
        }
        if self._report_path:
            payload["report_path"] = self._report_path
        return payload

    def _publish(self, *, interval_seconds: float = 30.0) -> None:
        payload = self.snapshot()
        try:
            Path(self.reports_dir).mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.path, payload)
        except OSError:
            pass
        try:
            write_heartbeat(
                self.state_root,
                "dojo",
                interval_seconds=interval_seconds,
                detail=f"{payload['phase_label']}: {payload['detail']}",
                extra={
                    "status": payload["status"],
                    "phase": payload["phase"],
                    "report_date": payload["report_date"],
                    "elapsed_seconds": payload["elapsed_seconds"],
                },
            )
        except OSError:
            pass


def idle_dojo_progress() -> dict[str, Any]:
    """Payload the API serves when no run has published ``progress.json``."""
    return {
        "status": "idle",
        "phase": "idle",
        "phase_label": "Idle",
        "detail": "Dojo is not running",
        "report_date": "",
        "started_at": None,
        "updated_at": None,
        "elapsed_seconds": 0,
        "pid": None,
        "phases": [
            {
                "name": name,
                "label": _PHASE_LABELS[name],
                "status": "pending",
                "detail": "",
            }
            for name in PHASES
        ],
        "live": False,
    }


def _age_seconds(updated_at: Any) -> float | None:
    if not isinstance(updated_at, str) or not updated_at:
        return None
    try:
        stamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return max(0.0, (_now() - stamp).total_seconds())


def annotate_dojo_progress(payload: dict[str, Any]) -> dict[str, Any]:
    """Mark abandoned runs and whether the UI should treat this as live."""
    out = dict(payload)
    status = str(out.get("status") or "")
    age = _age_seconds(out.get("updated_at"))
    live = status == "running" and (age is None or age <= STALE_AFTER_SECONDS)
    if status == "running" and not live:
        out["status"] = "stale"
        out["phase_label"] = out.get("phase_label") or _PHASE_LABELS.get(
            str(out.get("phase") or ""), "Dojo"
        )
        detail = str(out.get("detail") or "Dojo was running")
        out["detail"] = (
            f"{detail} — no update for {int(age or 0)}s; process may have stopped"
        )
    out["live"] = live
    if age is not None:
        out["seconds_since_update"] = round(age, 1)
    return out


def read_dojo_progress(reports_dir: str | Path) -> dict[str, Any] | None:
    """Latest progress payload, or ``None`` when no run has published one."""
    path = Path(reports_dir) / PROGRESS_FILENAME
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return annotate_dojo_progress(data)
