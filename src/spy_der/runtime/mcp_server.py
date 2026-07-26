"""Read-only MCP server — lets a model query SPY-DER without touching decisions.

Research questions ("what did the Dojo flag last night?", "is the feed stale?",
"is the gap model or execution?") are answerable from files already under the
state root. Answering them by hand means SSH, `jq`, and a human transcribing
numbers into a chat window. This exposes the same read surface over MCP so a
model can pull it directly.

**Read-only by construction, not by convention.** Every tool here is a thin
wrapper over `dashboard_api.handle_get` — the same pure handler the HTTP
dashboard uses, whose only capability is opening files under the state root.
There is no tool that writes, decides, sizes, sizes up, submits, or promotes,
and `tests/unit/test_mcp_server.py` fails if one is added: the decision path is
reachable only through the deterministic guard, and a chat transport must not
become a second way in. One read path, one set of semantics, one place to fix a
bug — adding a route to `handle_get` surfaces it here for free.

Transport is newline-delimited JSON-RPC 2.0 over stdio, implemented against the
stdlib. That is deliberate: `agents/transport.py` already speaks HTTP to xAI
without a vendor SDK, and this package's runtime dependency list is five
scientific libraries. A dashboard reader is not worth changing that.

Wire it into a client with:

    {"mcpServers": {"spy-der": {"command": "spy-der-mcp",
                                "args": ["--state-root", "/var/lib/spy-der"]}}}
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlencode

from spy_der.dojo.config import DEFAULT_STATE_ROOT
from spy_der.runtime.dashboard_api import DashboardApiState, handle_get

__all__ = [
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "TOOLS",
    "McpTool",
    "handle_message",
    "main",
    "serve",
    "tool_definitions",
]

SERVER_NAME = "spy-der"
SERVER_VERSION = "0.1.0"

#: Advertised when the client asks for something we do not know. MCP requires a
#: single concrete version in the initialize result, never a range.
PROTOCOL_VERSION = "2025-06-18"

#: Versions whose request shape this server handles. A client asking for one of
#: these gets it echoed back; anything else is answered with PROTOCOL_VERSION
#: and left to the client to accept or disconnect, which is what the spec
#: prescribes for version mismatch.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

# JSON-RPC 2.0 error codes.
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603


@dataclass(frozen=True, slots=True)
class McpTool:
    """One exposed tool, and the read it maps to.

    `route` and `query` are resolved against `dashboard_api.handle_get`, so a
    tool cannot express anything the read-only HTTP API cannot.
    """

    name: str
    title: str
    description: str
    route: str
    schema: Mapping[str, Any]
    query: Callable[[Mapping[str, Any]], str] | None = None


def _limit_query(arguments: Mapping[str, Any]) -> str:
    raw = arguments.get("limit", 20)
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        limit = 20
    return urlencode({"limit": max(1, min(limit, 200))})


_NO_ARGS: Mapping[str, Any] = {"type": "object", "properties": {}, "required": []}

_LIMIT_ARGS: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
            "default": 20,
            "description": "How many stamped reports to index, newest first.",
        }
    },
    "required": [],
}


TOOLS: tuple[McpTool, ...] = (
    McpTool(
        name="spy_der_system_status",
        title="System status",
        description=(
            "Everything an operator would otherwise SSH for, in one read: which "
            "services are publishing heartbeats, whether market data is "
            "arriving, whether the AI gate is open, and what the last deploy "
            "published. Start here when asked whether SPY-DER is working."
        ),
        route="/v1/system",
        schema=_NO_ARGS,
    ),
    McpTool(
        name="spy_der_live_state",
        title="Live decision state",
        description=(
            "The current published decision packet (schema spyder.dashboard.v1): "
            "action, candidate id, confidence, uncertainty, size scalar, reason "
            "codes, trader/reviewer model, and Dojo status. This is what the "
            "dashboard renders."
        ),
        route="/v1/state",
        schema=_NO_ARGS,
    ),
    McpTool(
        name="spy_der_dojo_latest",
        title="Latest Dojo report",
        description=(
            "Newest completed Dojo report: per-phase status, flags (weak "
            "archetypes, regressions) and metrics. Use for 'what did last "
            "night's run find?'."
        ),
        route="/v1/dojo/latest",
        schema=_NO_ARGS,
    ),
    McpTool(
        name="spy_der_dojo_reports",
        title="Dojo report index",
        description=(
            "Index of stamped Dojo reports, newest first, with date, summary and "
            "flag count. Use to spot a trend across runs rather than one night."
        ),
        route="/v1/dojo/reports",
        schema=_LIMIT_ARGS,
        query=_limit_query,
    ),
    McpTool(
        name="spy_der_validation_latest",
        title="Latest parity validation",
        description=(
            "Newest parity-validation report: per-gate results and the top-level "
            "verdict. This is the migration's parity evidence — use it before "
            "claiming a capability has moved."
        ),
        route="/v1/validation/latest",
        schema=_NO_ARGS,
    ),
    McpTool(
        name="spy_der_validation_reports",
        title="Validation report index",
        description=(
            "Index of stamped validation reports, newest first, with gate counts "
            "and pass/fail."
        ),
        route="/v1/validation/reports",
        schema=_LIMIT_ARGS,
        query=_limit_query,
    ),
    McpTool(
        name="spy_der_attribution",
        title="Shadow-account attribution",
        description=(
            "Latest shadow-account report: the model book and the actual book "
            "scored separately, with the gap decomposed into participation, "
            "selection, sizing, entry and exit, plus behavioural flag counts. "
            "Use to answer whether a drawdown came from the forecast or from "
            "execution — the verdict field names which."
        ),
        route="/v1/attribution/latest",
        schema=_NO_ARGS,
    ),
)


def tool_definitions() -> list[dict[str, Any]]:
    """`tools/list` payload."""
    return [
        {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "inputSchema": dict(tool.schema),
            # Advertise the read-only guarantee to the client so a host can
            # surface it without needing to trust the description prose.
            "annotations": {
                "title": tool.title,
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        }
        for tool in TOOLS
    ]


_BY_NAME = {tool.name: tool for tool in TOOLS}


def _call_tool(state: DashboardApiState, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Run one tool. A miss on disk is a tool error, not a protocol error."""
    tool = _BY_NAME.get(name)
    if tool is None:
        return _tool_error(f"unknown tool: {name}")
    query = tool.query(arguments) if tool.query is not None else ""
    try:
        code, body = handle_get(state, tool.route, query)
    except PermissionError as exc:
        # The recurring production failure: state written 0600 by another unit.
        # Name it, because "no data" would send the reader looking in the wrong
        # place entirely.
        return _tool_error(f"state unreadable ({exc}); check permissions under the state root")
    except Exception as exc:  # a bad file must not kill the session
        return _tool_error(f"read failed: {type(exc).__name__}: {exc}")

    text = json.dumps(body, indent=2, default=str, sort_keys=True)
    return {
        "content": [{"type": "text", "text": text}],
        # 404 is a real answer here ("no Dojo run has completed"), so it is
        # returned as an error result rather than as ordinary content — a model
        # should not read an absence as data.
        "isError": code >= 400,
        "structuredContent": body if code < 400 else {"error": body},
    }


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _negotiated_version(requested: Any) -> str:
    return requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION


def handle_message(
    state: DashboardApiState, message: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Returns None for notifications.

    Pure: no I/O beyond the state-root reads the tools perform, so the whole
    protocol surface is unit-testable without spawning a process or a pipe.
    """
    if message.get("jsonrpc") != "2.0":
        return _error(message.get("id"), _INVALID_REQUEST, "jsonrpc must be '2.0'")

    method = message.get("method")
    if not isinstance(method, str):
        return _error(message.get("id"), _INVALID_REQUEST, "method must be a string")

    message_id = message.get("id")
    # Notifications (no id) get no response, per JSON-RPC. `notifications/*`
    # from the client — initialized, cancelled — land here and are dropped.
    if message_id is None:
        return None

    params = message.get("params")
    params = params if isinstance(params, Mapping) else {}

    if method == "initialize":
        return _result(
            message_id,
            {
                "protocolVersion": _negotiated_version(params.get("protocolVersion")),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": SERVER_NAME,
                    "title": "SPY-DER (read-only)",
                    "version": SERVER_VERSION,
                },
                "instructions": (
                    "Read-only view of a SPY-DER deployment. Every tool reads "
                    "published state under the state root; none can place, "
                    "size, approve or promote anything. Deterministic risk and "
                    "the execution guard remain the only path to a trade."
                ),
            },
        )
    if method == "ping":
        return _result(message_id, {})
    if method == "tools/list":
        return _result(message_id, {"tools": tool_definitions()})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return _error(message_id, _INVALID_REQUEST, "params.name must be a string")
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, Mapping) else {}
        return _result(message_id, _call_tool(state, name, arguments))
    return _error(message_id, _METHOD_NOT_FOUND, f"unknown method: {method}")


def _result(message_id: Any, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": dict(result)}


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def serve(
    state_root: str | Path = DEFAULT_STATE_ROOT,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Serve newline-delimited JSON-RPC over stdio until EOF."""
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    state = DashboardApiState(state_root)

    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(sink, _error(None, _PARSE_ERROR, f"invalid JSON: {exc}"))
            continue
        if not isinstance(message, dict):
            _write(sink, _error(None, _INVALID_REQUEST, "message must be an object"))
            continue
        try:
            response = handle_message(state, message)
        except Exception as exc:  # never take the session down
            _write(
                sink,
                _error(
                    message.get("id"),
                    _INTERNAL_ERROR,
                    f"{type(exc).__name__}: {exc}",
                ),
            )
            continue
        if response is not None:
            _write(sink, response)
    return 0


def _write(sink: TextIO, payload: Mapping[str, Any]) -> None:
    sink.write(json.dumps(payload, default=str) + "\n")
    sink.flush()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="SPY-DER read-only MCP server (stdio) over published state"
    )
    ap.add_argument("--state-root", default=DEFAULT_STATE_ROOT)
    args = ap.parse_args(argv)
    return serve(args.state_root)


if __name__ == "__main__":
    raise SystemExit(main())
