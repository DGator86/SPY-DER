"""Read-only HTTP dashboard API — serves Dojo reports and live state.

`deploy/spy-der-dashboard-api.service` has shipped an ExecStart for
``spy-der dashboard-api`` since the cutover plan was written, but the command
did not exist, so the unit exited 2 on every start. A Dojo run would write
``reports/dojo/latest.json`` and nothing was ever able to serve it.

The service is deliberately read-only: it opens files under the state root and
never writes. The unit enforces that with ``ReadOnlyPaths=/var/lib/spy-der``.

Routes:
    GET /health                    liveness
    GET /v1/system                 services, feed, AI gate, deploy — one view
    GET /v1/state                  live_state.json (spyder.dashboard.v1)
    GET /v1/dojo/progress          live Dojo phase strip (working / idle / stale)
    GET /v1/dojo/latest            newest Dojo report
    GET /v1/dojo/reports           index of stamped Dojo reports (newest first)
    GET /v1/validation/latest      newest parity-validation report
    GET /v1/validation/reports     index of stamped validation reports
    GET /v1/attribution/latest     newest shadow-account attribution report
    GET /v1/attribution/reports    index of stamped attribution reports
    GET /ui                        the SPY-DER dashboard tab, standalone
    GET /ui/<asset>                its stylesheet and module

`handle_get` is the single read path for JSON: `spy_der.runtime.mcp_server` wraps
the same function, so a route added here is available over MCP without a second
implementation — and cannot acquire write access on either transport.

`/ui` is served separately by the HTTP handler because it returns HTML, CSS and
JavaScript rather than a JSON object. It is deliberately kept out of `handle_get`
so the MCP surface stays JSON-only. The assets under `ui/` are the same files the
0DTE Vercel dashboard embeds as a tab — see
`integrations/zerodte/spy_der_tab/README.md`.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from spy_der.dojo.config import DEFAULT_STATE_ROOT
from spy_der.dojo.progress import idle_dojo_progress, read_dojo_progress
from spy_der.dojo.reports import read_latest_dojo_report

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "UI_CONTENT_TYPES",
    "UI_ROOT",
    "DashboardApiState",
    "handle_get",
    "main",
    "read_ui_asset",
    "serve",
]

#: Installed alongside this module, so the assets ship with the package and the
#: unit is not depending on a checkout being present on the VPS.
UI_ROOT = Path(__file__).resolve().parent / "ui"

UI_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}

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
        self.validation_dir = self.state_root / "reports" / "validation"
        self.attribution_dir = self.state_root / "reports" / "attribution"
        self.live_state_path = self.state_root / "live_state.json"

    def live_state(self) -> dict[str, Any] | None:
        if not self.live_state_path.is_file():
            return None
        with open(self.live_state_path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None

    def latest_dojo_report(self) -> dict[str, Any] | None:
        return read_latest_dojo_report(self.reports_dir)

    def dojo_progress(self) -> dict[str, Any]:
        """Live Dojo square. Always 200 — idle when no run has published yet."""
        body = read_dojo_progress(self.reports_dir)
        return body if body is not None else idle_dojo_progress()

    def latest_validation_report(self) -> dict[str, Any] | None:
        latest = self.validation_dir / "latest.json"
        if not latest.is_file():
            return None
        with open(latest, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None

    def latest_attribution_report(self) -> dict[str, Any] | None:
        latest = self.attribution_dir / "latest.json"
        if not latest.is_file():
            return None
        with open(latest, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None

    def dojo_report_index(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._report_index(self.reports_dir, "dojo_", limit)

    def validation_report_index(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._report_index(self.validation_dir, "validation_", limit)

    def attribution_report_index(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._report_index(self.attribution_dir, "attribution_", limit)

    def _report_index(
        self, directory: Path, prefix: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Stamped reports newest first. Names sort chronologically by design."""
        if not directory.is_dir():
            return []
        stamped = sorted(
            directory.glob(f"{prefix}*.json"),
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
                # Dojo reports carry flags; validation reports carry gates and a
                # top-level verdict. Index whichever this report actually has.
                if "flags" in body:
                    entry["flags"] = len(body.get("flags") or [])
                if "gates" in body:
                    entry["gates"] = len(body.get("gates") or [])
                    entry["ok"] = body.get("ok")
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
    if path in {"/v1/dojo/progress"}:
        return 200, state.dojo_progress()
    if path in {"/v1/system", "/v1/status"}:
        from spy_der.runtime.system_status import build_system_status

        return 200, build_system_status(state.state_root)
    if path in {"/v1/validation/latest", "/v1/validation"}:
        body = state.latest_validation_report()
        if body is None:
            return 404, {
                "error": "no_validation_report",
                "path": str(state.validation_dir / "latest.json"),
                "detail": "no validation run has completed, or its report is unreadable",
            }
        return 200, body
    if path in {"/v1/attribution/latest", "/v1/attribution"}:
        body = state.latest_attribution_report()
        if body is None:
            return 404, {
                "error": "no_attribution_report",
                "path": str(state.attribution_dir / "latest.json"),
                "detail": (
                    "no shadow-account report has been written, or it is "
                    "unreadable"
                ),
            }
        return 200, body
    if path in {
        "/v1/dojo/reports",
        "/v1/validation/reports",
        "/v1/attribution/reports",
    }:
        raw = (params.get("limit") or ["20"])[0]
        try:
            limit = int(raw)
        except ValueError:
            return 400, {"error": "invalid_limit", "got": raw}
        indexers = {
            "/v1/dojo/reports": state.dojo_report_index,
            "/v1/validation/reports": state.validation_report_index,
            "/v1/attribution/reports": state.attribution_report_index,
        }
        return 200, {"reports": indexers[path](limit)}
    return 404, {"error": "not_found", "path": path}


def read_ui_asset(path: str) -> tuple[int, str, bytes]:
    """Resolve a `/ui...` path to (status, content type, body).

    Path handling is restrictive on purpose: this is the only part of the API
    that maps a request path onto a filename, so it takes the basename, checks
    the suffix against a fixed allowlist, and confirms the resolved file is
    inside `UI_ROOT`. Traversal is therefore rejected three independent ways —
    the service is read-only, but read-only over the wrong directory is still a
    disclosure.
    """
    name = path[len("/ui") :].lstrip("/") or "index.html"
    if "/" in name or "\\" in name:
        return 404, "text/plain; charset=utf-8", b"not found"
    suffix = Path(name).suffix
    content_type = UI_CONTENT_TYPES.get(suffix)
    if content_type is None:
        return 404, "text/plain; charset=utf-8", b"not found"
    candidate = (UI_ROOT / name).resolve()
    if candidate.parent != UI_ROOT or not candidate.is_file():
        return 404, "text/plain; charset=utf-8", b"not found"
    return 200, content_type, candidate.read_bytes()


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

    def _send_bytes(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/ui" or parsed.path.startswith("/ui/"):
            try:
                code, content_type, body = read_ui_asset(parsed.path)
            except OSError as exc:
                self._send(503, {"error": "ui_unreadable", "detail": str(exc)})
                return
            self._send_bytes(code, content_type, body)
            return
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
