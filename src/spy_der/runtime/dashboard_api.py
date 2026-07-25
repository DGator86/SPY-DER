"""Read-only HTTP dashboard API — serves Dojo reports and live state.

`deploy/spy-der-dashboard-api.service` has shipped an ExecStart for
``spy-der dashboard-api`` since the cutover plan was written, but the command
did not exist, so the unit exited 2 on every start. A Dojo run would write
``reports/dojo/latest.json`` and nothing was ever able to serve it.

The service is deliberately read-only: it opens files under the state root and
never writes. The unit enforces that with ``ReadOnlyPaths=/var/lib/spy-der``.

Routes:
    GET /health              liveness
    GET /v1/state            live_state.json (spyder.dashboard.v1)
    GET /v1/dojo/latest      newest Dojo report
    GET /v1/dojo/reports     index of stamped Dojo reports (newest first)
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from spy_der.dojo.config import DEFAULT_STATE_ROOT
from spy_der.dojo.reports import read_latest_dojo_report

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DashboardApiState",
    "handle_get",
    "main",
    "serve",
]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8788

#: Cap on `/v1/dojo/reports` so a long-lived state directory cannot produce an
#: unbounded response.
MAX_REPORT_INDEX = 200


class DashboardApiState:
    """Filesystem locations the API reads, derived from one state root."""

    def __init__(self, state_root: str | Path = DEFAULT_STATE_ROOT) -> None:
        self.state_root = Path(state_root)
        self.reports_dir = self.state_root / "reports" / "dojo"
        self.live_state_path = self.state_root / "live_state.json"

    def live_state(self) -> dict[str, Any] | None:
        if not self.live_state_path.is_file():
            return None
        with open(self.live_state_path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None

    def latest_dojo_report(self) -> dict[str, Any] | None:
        return read_latest_dojo_report(self.reports_dir)

    def dojo_report_index(self, limit: int = 20) -> list[dict[str, Any]]:
        """Stamped reports newest first. Names sort chronologically by design."""
        if not self.reports_dir.is_dir():
            return []
        stamped = sorted(
            self.reports_dir.glob("dojo_*.json"),
            key=lambda p: p.name,
            reverse=True,
        )
        index: list[dict[str, Any]] = []
        for path in stamped[: max(0, min(limit, MAX_REPORT_INDEX))]:
            entry: dict[str, Any] = {"name": path.name, "path": str(path)}
            try:
                with open(path, encoding="utf-8") as handle:
                    body = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                # A truncated report must not take the whole index down.
                entry["error"] = f"{type(exc).__name__}"
                index.append(entry)
                continue
            if isinstance(body, dict):
                entry["report_date"] = body.get("report_date")
                entry["generated_at"] = body.get("generated_at")
                entry["summary"] = body.get("summary")
                entry["flags"] = len(body.get("flags") or [])
            index.append(entry)
        return index


def handle_get(state: DashboardApiState, path: str, query: str = "") -> tuple[int, dict[str, Any]]:
    """Pure request handler — unit-testable without binding a socket."""
    params = parse_qs(query)
    if path in {"/health", "/v1/health"}:
        return 200, {
            "status": "ok",
            "service": "spy-der-dashboard-api",
            "state_root": str(state.state_root),
        }
    if path in {"/v1/state", "/v1/live_state"}:
        body = state.live_state()
        if body is None:
            return 404, {"error": "no_live_state", "path": str(state.live_state_path)}
        return 200, body
    if path in {"/v1/dojo/latest", "/v1/dojo"}:
        body = state.latest_dojo_report()
        if body is None:
            return 404, {
                "error": "no_dojo_report",
                "path": str(state.reports_dir / "latest.json"),
                "detail": "no Dojo run has completed, or its report is unreadable",
            }
        return 200, body
    if path == "/v1/dojo/reports":
        raw = (params.get("limit") or ["20"])[0]
        try:
            limit = int(raw)
        except ValueError:
            return 400, {"error": "invalid_limit", "got": raw}
        return 200, {"reports": state.dojo_report_index(limit)}
    return 404, {"error": "not_found", "path": path}


class _DashboardHandler(BaseHTTPRequestHandler):
    server_version = "SpyDerDashboardApi/1.0"
    state: DashboardApiState = DashboardApiState()

    def log_message(self, format: str, *args: Any) -> None:
        # Keep journal noise low; systemd captures stdout separately if needed.
        return

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            code, payload = handle_get(self.state, parsed.path, parsed.query)
        except PermissionError as exc:
            # The most common production failure: state written 0600 by another
            # unit. Say so explicitly instead of a bare 500.
            self._send(503, {"error": "state_unreadable", "detail": str(exc)})
            return
        except Exception as exc:  # fail closed, stay up
            self._send(500, {"error": "read_failed", "detail": f"{type(exc).__name__}:{exc}"})
            return
        self._send(code, payload)


def serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    state_root: str | Path = DEFAULT_STATE_ROOT,
) -> None:
    _DashboardHandler.state = DashboardApiState(state_root)
    server = ThreadingHTTPServer((host, port), _DashboardHandler)
    print(f"spy-der dashboard api listening on http://{host}:{port} (state={state_root})")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="SPY-DER read-only dashboard API (Dojo reports + live state)"
    )
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--state-root", default=DEFAULT_STATE_ROOT)
    args = ap.parse_args(argv)
    serve(args.host, args.port, args.state_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
