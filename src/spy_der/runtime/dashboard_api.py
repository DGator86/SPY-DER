"""HTTP dashboard API — Dojo reports, live state, and operator promotion.

Reads under the state root are always available. Writes (promote / reject /
rollback) require ``SPY_DER_OPERATOR_TOKEN`` and only touch
``state_root/configs/``. The unit keeps the rest of the state root read-only
via ``ReadOnlyPaths`` + ``ReadWritePaths=…/configs``.

Routes:
    GET  /health                       liveness
    GET  /v1/system                    services, feed, AI gate, deploy — one view
    GET  /v1/state                     live_state.json (spyder.dashboard.v1)
    GET  /v1/dojo/progress             live Dojo phase strip (working / idle / stale)
    GET  /v1/dojo/latest               newest Dojo report
    GET  /v1/dojo/reports              index of stamped Dojo reports (newest first)
    GET  /v1/dojo/pending              staged knob challengers (pending_review/)
    GET  /v1/dojo/champion             current champion.json knobs (or 404)
    POST /v1/dojo/promote              operator: promote pending → champion
    POST /v1/dojo/reject               operator: reject a pending challenger
    POST /v1/dojo/rollback             operator: restore previous champion
    GET  /v1/validation/latest         newest parity-validation report
    GET  /v1/validation/reports        index of stamped validation reports
    GET  /v1/attribution/latest        newest shadow-account attribution report
    GET  /v1/attribution/reports       index of stamped attribution reports
    GET  /ui                           SPY-DER dashboard (primary Adaptive Loop UI)
    GET  /ui/<asset>                   its stylesheet and module

`handle_get` is the single read path for JSON: `spy_der.runtime.mcp_server` wraps
the same function, so a route added here is available over MCP without a second
implementation — and cannot acquire write access on either transport. Writes go
through `handle_post` only, which MCP never calls.

`/ui` is served separately by the HTTP handler because it returns HTML, CSS and
JavaScript rather than a JSON object. It is deliberately kept out of `handle_get`
so the MCP surface stays JSON-only. The assets under `ui/` are the same files the
0DTE Vercel dashboard embeds as a tab — see
`integrations/zerodte/spy_der_tab/README.md`.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from spy_der.dojo.config import DEFAULT_STATE_ROOT
from spy_der.dojo.progress import idle_dojo_progress, read_dojo_progress
from spy_der.dojo.reports import read_latest_dojo_report
from spy_der.learning.promotion import (
    PromotionError,
    current_champion,
    list_pending,
    promote_pending,
    reject_pending,
    rollback_champion,
)

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "OPERATOR_TOKEN_ENV",
    "UI_CONTENT_TYPES",
    "UI_ROOT",
    "DashboardApiState",
    "handle_get",
    "handle_post",
    "main",
    "operator_token_configured",
    "read_ui_asset",
    "serve",
    "verify_operator_token",
]

OPERATOR_TOKEN_ENV = "SPY_DER_OPERATOR_TOKEN"

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
        # Knob challengers and champion.json live under configs/ — the only
        # subdirectory the operator write routes may mutate.
        self.configs_dir = self.state_root / "configs"

    def pending_challengers(self) -> list[dict[str, Any]]:
        """Serialize staged knob challengers for the Adaptive Loop panel."""
        out: list[dict[str, Any]] = []
        for candidate in list_pending(self.configs_dir):
            payload = dict(candidate.payload)
            knobs = payload.get("knobs") if isinstance(payload.get("knobs"), dict) else {}
            out.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "status": payload.get("status") or "pending_review",
                    "auto_promote": bool(payload.get("auto_promote")),
                    "knobs": knobs,
                    "mode": payload.get("mode"),
                    "target_archetype": payload.get("target_archetype"),
                    "gates": payload.get("gates") or [],
                    "hypothesis": payload.get("hypothesis"),
                    "experience_summary": payload.get("experience_summary"),
                    "sequential": payload.get("sequential"),
                }
            )
        return out

    def champion(self) -> dict[str, Any] | None:
        return current_champion(self.configs_dir)

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
    if path in {"/v1/dojo/pending", "/v1/dojo/challengers"}:
        pending = state.pending_challengers()
        return 200, {
            "pending": pending,
            "count": len(pending),
            "configs_dir": str(state.configs_dir),
            "actions_enabled": operator_token_configured(),
            "lane": "decision_knobs",
        }
    if path in {"/v1/dojo/champion", "/v1/champion"}:
        body = state.champion()
        if body is None:
            return 404, {
                "error": "no_champion",
                "path": str(state.configs_dir / "champion.json"),
                "detail": "no knob champion has been promoted yet",
            }
        return 200, body
    return 404, {"error": "not_found", "path": path}


def operator_token_configured() -> bool:
    token = os.environ.get(OPERATOR_TOKEN_ENV, "").strip()
    return bool(token)


def verify_operator_token(
    authorization: str | None,
    *,
    operator_header: str | None = None,
) -> bool:
    """Accept Bearer token or ``X-Spy-Der-Operator-Token`` matching the env."""
    expected = os.environ.get(OPERATOR_TOKEN_ENV, "").strip()
    if not expected:
        return False
    candidates: list[str] = []
    if operator_header and operator_header.strip():
        candidates.append(operator_header.strip())
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            candidates.append(value.strip())
        elif authorization.strip():
            # Raw token without scheme — tolerate for the X-header path.
            candidates.append(authorization.strip())
    return any(hmac.compare_digest(candidate, expected) for candidate in candidates)


def handle_post(
    state: DashboardApiState,
    path: str,
    body: dict[str, Any] | None,
    *,
    authorization: str | None = None,
    operator_header: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Operator write path — promote / reject / rollback knob challengers.

    Never reachable from MCP. Requires ``SPY_DER_OPERATOR_TOKEN``. Does not
    place trades; it only mutates ``configs/champion.json`` and pending review.
    """
    if path not in {
        "/v1/dojo/promote",
        "/v1/dojo/reject",
        "/v1/dojo/rollback",
    }:
        return 404, {"error": "not_found", "path": path}
    if not operator_token_configured():
        return 503, {
            "error": "operator_token_unset",
            "detail": (
                f"set {OPERATOR_TOKEN_ENV} on spy-der-dashboard-api to enable "
                "Promote / Reject / Rollback from the Adaptive Loop panel"
            ),
        }
    if not verify_operator_token(authorization, operator_header=operator_header):
        return 401, {
            "error": "unauthorized",
            "detail": "Bearer token required for operator writes",
        }

    payload = body if isinstance(body, dict) else {}
    try:
        if path == "/v1/dojo/promote":
            candidate_id = str(payload.get("candidate_id") or "").strip()
            if not candidate_id:
                return 400, {"error": "candidate_id_required"}
            ack = str(payload.get("human_ack") or payload.get("ack") or "PROMOTE")
            champion = promote_pending(
                state.configs_dir, candidate_id, human_ack=ack
            )
            return 200, {
                "ok": True,
                "action": "promote",
                "candidate_id": candidate_id,
                "lane": "decision_knobs",
                "champion_path": str(champion),
                "champion": current_champion(state.configs_dir),
                "pending": state.pending_challengers(),
            }
        if path == "/v1/dojo/reject":
            candidate_id = str(payload.get("candidate_id") or "").strip()
            if not candidate_id:
                return 400, {"error": "candidate_id_required"}
            reject_pending(state.configs_dir, candidate_id)
            return 200, {
                "ok": True,
                "action": "reject",
                "candidate_id": candidate_id,
                "lane": "decision_knobs",
                "pending": state.pending_challengers(),
            }
        # /v1/dojo/rollback
        restored = rollback_champion(state.configs_dir)
        if restored is None:
            return 404, {
                "error": "no_champion_history",
                "detail": "nothing to roll back — champion_history/ is empty",
            }
        return 200, {
            "ok": True,
            "action": "rollback",
            "lane": "decision_knobs",
            "champion_path": str(restored),
            "champion": current_champion(state.configs_dir),
            "pending": state.pending_challengers(),
        }
    except PromotionError as exc:
        return 409, {"error": "promotion_refused", "detail": str(exc)}
    except OSError as exc:
        return 503, {
            "error": "configs_unwritable",
            "detail": str(exc),
            "configs_dir": str(state.configs_dir),
        }


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

    def _read_json_body(self) -> tuple[dict[str, Any] | None, str | None]:
        raw_len = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_len)
        except ValueError:
            return None, "invalid Content-Length"
        if length <= 0:
            return {}, None
        if length > 1_000_000:
            return None, "body too large"
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return None, f"invalid json: {exc}"
        if data is None:
            return {}, None
        if not isinstance(data, dict):
            return None, "JSON body must be an object"
        return data, None

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

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        body, err = self._read_json_body()
        if err is not None:
            self._send(400, {"error": "invalid_body", "detail": err})
            return
        try:
            code, payload = handle_post(
                self.state,
                parsed.path,
                body,
                authorization=self.headers.get("Authorization"),
                operator_header=self.headers.get("X-Spy-Der-Operator-Token"),
            )
        except Exception as exc:  # fail closed, stay up
            self._send(
                500, {"error": "write_failed", "detail": f"{type(exc).__name__}:{exc}"}
            )
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
        description=(
            "SPY-DER dashboard API (Dojo reports + live state + operator "
            "promote/reject)"
        )
    )
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--state-root", default=DEFAULT_STATE_ROOT)
    args = ap.parse_args(argv)
    serve(args.host, args.port, args.state_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
