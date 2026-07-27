"""One view of everything an operator would otherwise SSH to check.

Answering "is it working?" meant `systemctl status`, `journalctl`, `ls -l` and
`git rev-parse` over SSH. Every one of those answers is derivable from files
already under the state root, so it belongs on the dashboard instead.

Four questions, four sections:

``services``  is each service alive?          — heartbeats
``feed``      is market data arriving?        — the session recording itself
``ai``        is the model allowed to run?    — the live gate + last decision
``deploy``    did my change land?             — what the deploy published

Read-only and defensive throughout: a missing or unreadable file becomes a note
on that section, never an exception. A dashboard that 500s because one file is
absent is worse than one reporting "unknown".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spy_der.runtime.heartbeat import read_heartbeats

__all__ = ["ServiceExpectation", "build_system_status"]

#: Services expected to publish a heartbeat, with what they are for. A service
#: that has never written one is reported "never_seen" rather than omitted —
#: silence about a service that should exist is the failure being hidden.
EXPECTED_SERVICES: dict[str, str] = {
    "market": "provider ingestion → market/",
    "engine": "deterministic stages → candidates/ + journal",
    "settlement": "session settlement → settlements/ + journal",
    # Decision boundary that 0DTE POSTs to. Without this, market/engine/settlement
    # can all read "OK" while SPY-DER is offline on the dashboard — the exact
    # failure mode the System panel exists to catch.
    "agent": "decision HTTP boundary → :8787 + live_state",
}


class ServiceExpectation:
    """Names the services a healthy deployment publishes."""

    names = tuple(EXPECTED_SERVICES)


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None, "not found"
    except PermissionError:
        return None, "permission denied"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"unreadable ({type(exc).__name__})"
    return (data, None) if isinstance(data, dict) else (None, "not a JSON object")


def _age_seconds(value: Any, now: datetime) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (now - parsed).total_seconds())


def _services(state_root: Path, now: datetime) -> list[dict[str, Any]]:
    published = {entry["service"]: entry for entry in read_heartbeats(state_root, now=now)}
    out: list[dict[str, Any]] = []
    for name, purpose in EXPECTED_SERVICES.items():
        entry = published.pop(name, None)
        if entry is None:
            out.append(
                {
                    "service": name,
                    "purpose": purpose,
                    "state": "never_seen",
                    "detail": "no heartbeat has ever been published",
                }
            )
            continue
        entry["purpose"] = purpose
        out.append(entry)
    # Anything else that published a heartbeat still gets reported.
    for entry in published.values():
        entry.setdefault("purpose", "")
        out.append(entry)
    return out


def _feed(state_root: Path, now: datetime) -> dict[str, Any]:
    """Recording progress read from the tape, not from a service's self-report.

    A heartbeat says the loop is turning; this says data actually landed. They
    disagree exactly when something is wrong — a running feed recording nothing
    is the failure mode worth surfacing.
    """
    market = state_root / "market"
    if not market.is_dir():
        return {"state": "no_recordings", "note": f"{market} does not exist"}
    sessions = sorted(p.stem for p in market.glob("*.jsonl"))
    if not sessions:
        return {"state": "no_recordings", "note": "no session recordings yet"}

    latest = sessions[-1]
    path = market / f"{latest}.jsonl"
    ticks = 0
    last_line = ""
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    ticks += 1
                    last_line = line
    except PermissionError:
        return {"state": "unreadable", "session": latest, "note": "permission denied"}
    except OSError as exc:
        return {"state": "unreadable", "session": latest, "note": str(exc)}

    observed_at: str | None = None
    provider: str | None = None
    if last_line:
        try:
            snapshot = (json.loads(last_line) or {}).get("snapshot") or {}
            observed_at = snapshot.get("timestamp")
            selected = snapshot.get("selected_providers") or []
            if selected and isinstance(selected[0], dict):
                provider = selected[0].get("provider")
        except json.JSONDecodeError:
            observed_at = None

    return {
        "state": "recording" if ticks else "empty",
        "session": latest,
        "sessions_recorded": len(sessions),
        "ticks": ticks,
        "last_tick_at": observed_at,
        "last_tick_age_seconds": _age_seconds(observed_at, now),
        "provider": provider,
    }


def _ai(state_root: Path, now: datetime) -> dict[str, Any]:
    """Whether the model may run right now, and who authored the last decision."""
    from spy_der.decisions.shadow import _ai_allowed_now, _market_hours_only

    allowed, reason = _ai_allowed_now(now)
    status: dict[str, Any] = {
        "allowed_now": allowed,
        "reason": reason,
        "market_hours_only": _market_hours_only(),
    }
    live, note = _read_json(state_root / "live_state.json")
    if live is None:
        status["last_decision"] = None
        status["note"] = f"live_state {note}"
        return status
    status["last_decision"] = {
        "action": live.get("action"),
        "trader_model": live.get("trader_model"),
        "reviewer_model": live.get("reviewer_model"),
        "provider": live.get("provider"),
        "generated_at": live.get("generated_at"),
        "age_seconds": _age_seconds(live.get("generated_at"), now),
    }
    return status


def _deploy(state_root: Path, now: datetime) -> dict[str, Any]:
    data, note = _read_json(state_root / "deploy.json")
    if data is None:
        return {"state": "unknown", "note": f"deploy.json {note}"}
    data["deployed_age_seconds"] = _age_seconds(data.get("deployed_at"), now)
    data["state"] = "ok"
    return data


def _reports(state_root: Path, now: datetime) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, relative in (
        ("dojo", Path("reports") / "dojo" / "latest.json"),
        ("validation", Path("reports") / "validation" / "latest.json"),
    ):
        data, note = _read_json(state_root / relative)
        if data is None:
            out[label] = {"state": "unavailable", "note": note}
            continue
        out[label] = {
            "state": "ok",
            "report_date": data.get("report_date"),
            "summary": data.get("summary"),
            "generated_at": data.get("generated_at"),
            "age_seconds": _age_seconds(data.get("generated_at"), now),
            "flags": len(data.get("flags") or []),
            "ok": data.get("ok"),
        }
    return out


def _overall(services: list[dict[str, Any]], feed: dict[str, Any]) -> str:
    """Worst-of, so a green banner cannot hide a stopped service."""
    states = {str(s.get("state")) for s in services}
    if states & {"stale", "never_seen", "failed"}:
        return "degraded"
    if feed.get("state") in {"unreadable"}:
        return "degraded"
    if states & {"late", "unknown"}:
        return "warn"
    if feed.get("state") in {"no_recordings", "empty"}:
        return "warn"
    return "ok"


def build_system_status(
    state_root: str | Path, *, now: datetime | None = None
) -> dict[str, Any]:
    """Everything the dashboard needs to replace an SSH session."""
    root = Path(state_root)
    stamp = now or datetime.now(tz=UTC)
    services = _services(root, stamp)
    feed = _feed(root, stamp)
    return {
        "generated_at": stamp.isoformat(),
        "state_root": str(root),
        "overall": _overall(services, feed),
        "services": services,
        "feed": feed,
        "ai": _ai(root, stamp),
        "deploy": _deploy(root, stamp),
        "reports": _reports(root, stamp),
    }
