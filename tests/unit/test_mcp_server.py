"""Read-only MCP server.

Two things are being protected here. The protocol behaviour, and — more
importantly — the read-only guarantee. `test_no_tool_can_write` and
`test_every_tool_routes_through_the_read_only_handler` are structural: they fail
if someone adds a tool that can act, which is the failure mode that matters. The
guard and deterministic risk are the only path to a trade; an MCP transport must
not become a second one.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from spy_der.dojo.reports import persist_dojo_report
from spy_der.evaluation.attribution import (
    ActualTrade,
    PlannedTrade,
    attribute_session,
)
from spy_der.evaluation.reports import persist_attribution_report
from spy_der.runtime.dashboard_api import DashboardApiState, handle_get
from spy_der.runtime.mcp_server import (
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    TOOLS,
    handle_message,
    serve,
    tool_definitions,
)


def _state(tmp_path: Path) -> DashboardApiState:
    return DashboardApiState(tmp_path)


def _rpc(
    tmp_path: Path, method: str, params: dict[str, Any] | None = None, *, id_: Any = 1
) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        message["params"] = params
    response = handle_message(_state(tmp_path), message)
    assert response is not None
    return response


def _call(tmp_path: Path, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    response = _rpc(
        tmp_path, "tools/call", {"name": name, "arguments": arguments or {}}
    )
    result = response["result"]
    assert isinstance(result, dict)
    return result


def _seed_attribution(tmp_path: Path) -> None:
    report = attribute_session(
        [
            (
                PlannedTrade(
                    candidate_id="cand-a",
                    contracts=10,
                    entry_price=Decimal("-0.50"),
                    exit_price=Decimal("0"),
                    snapshot_id="snap-1",
                    session_date="2026-07-24",
                ),
                ActualTrade(
                    candidate_id="cand-a",
                    contracts=10,
                    entry_price=Decimal("-0.40"),
                    exit_price=Decimal("0"),
                ),
            )
        ]
    )
    persist_attribution_report(tmp_path / "reports" / "attribution", report=report)


# --------------------------------------------------------------------------- #
# Read-only guarantee (structural)                                            #
# --------------------------------------------------------------------------- #
def test_no_tool_can_write() -> None:
    """A tool whose name suggests it acts is a design error, not a feature."""
    forbidden = (
        "write",
        "set",
        "put",
        "post",
        "delete",
        "submit",
        "order",
        "trade",
        "decide",
        "decision",
        "size",
        "promote",
        "approve",
        "execute",
        "veto",
        "override",
        "run",
    )
    for tool in TOOLS:
        for word in forbidden:
            assert word not in tool.name, (
                f"{tool.name!r} looks like it mutates state. The MCP surface is "
                "read-only; the execution guard is the only path to a trade."
            )


def test_every_tool_routes_through_the_read_only_handler() -> None:
    """Tools may only express reads `handle_get` already serves.

    Distinguishes "routed but the file is absent" (a 404 naming the artifact,
    which is fine against an empty state root) from "not a route at all" (a 404
    carrying `not_found`, which means the tool points at nothing).
    """
    for tool in TOOLS:
        code, body = handle_get(DashboardApiState("/nonexistent-state-root"), tool.route)
        assert not (code == 404 and body.get("error") == "not_found"), (
            f"{tool.name!r} points at {tool.route!r}, which handle_get does not "
            "route. Add the route to dashboard_api first."
        )


def test_tool_definitions_advertise_read_only_hints() -> None:
    for definition in tool_definitions():
        annotations = definition["annotations"]
        assert annotations["readOnlyHint"] is True
        assert annotations["destructiveHint"] is False


# --------------------------------------------------------------------------- #
# Handshake                                                                   #
# --------------------------------------------------------------------------- #
def test_initialize_reports_tool_capability(tmp_path: Path) -> None:
    result = _rpc(tmp_path, "initialize", {"protocolVersion": PROTOCOL_VERSION})["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["serverInfo"]["name"] == "spy-der"
    assert "Read-only" in result["instructions"]


def test_initialize_echoes_a_supported_older_version(tmp_path: Path) -> None:
    older = SUPPORTED_PROTOCOL_VERSIONS[-1]
    result = _rpc(tmp_path, "initialize", {"protocolVersion": older})["result"]
    assert result["protocolVersion"] == older


def test_initialize_falls_back_on_an_unknown_version(tmp_path: Path) -> None:
    result = _rpc(tmp_path, "initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION


def test_ping_answers_empty(tmp_path: Path) -> None:
    assert _rpc(tmp_path, "ping")["result"] == {}


def test_notifications_get_no_response(tmp_path: Path) -> None:
    message = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert handle_message(_state(tmp_path), message) is None


def test_wrong_jsonrpc_version_is_rejected(tmp_path: Path) -> None:
    response = handle_message(
        _state(tmp_path), {"jsonrpc": "1.0", "id": 1, "method": "ping"}
    )
    assert response is not None
    assert response["error"]["code"] == -32600


def test_unknown_method_is_method_not_found(tmp_path: Path) -> None:
    response = _rpc(tmp_path, "resources/list")
    assert response["error"]["code"] == -32601


# --------------------------------------------------------------------------- #
# tools/list                                                                  #
# --------------------------------------------------------------------------- #
def test_tools_list_exposes_every_tool(tmp_path: Path) -> None:
    tools = _rpc(tmp_path, "tools/list")["result"]["tools"]
    assert {t["name"] for t in tools} == {t.name for t in TOOLS}
    for definition in tools:
        assert definition["description"]
        assert definition["inputSchema"]["type"] == "object"


# --------------------------------------------------------------------------- #
# tools/call                                                                  #
# --------------------------------------------------------------------------- #
def test_system_status_is_served_from_an_empty_state_root(tmp_path: Path) -> None:
    # A fresh deployment must answer "unknown", never raise.
    result = _call(tmp_path, "spy_der_system_status")
    assert result["isError"] is False
    assert result["structuredContent"]


def test_dojo_latest_returns_the_persisted_report(tmp_path: Path) -> None:
    persist_dojo_report(
        tmp_path / "reports" / "dojo",
        report_date="2026-07-24",
        summary="recorded tape: ok",
        flags=[],
        metrics={"phases": {"recorded": {"status": "ok"}}},
    )
    result = _call(tmp_path, "spy_der_dojo_latest")
    assert result["isError"] is False
    assert result["structuredContent"]["report_date"] == "2026-07-24"
    assert "recorded tape" in result["content"][0]["text"]


def test_missing_report_is_a_tool_error_not_silent_emptiness(tmp_path: Path) -> None:
    # A model must not read an absent report as "nothing was flagged".
    result = _call(tmp_path, "spy_der_dojo_latest")
    assert result["isError"] is True
    assert "no_dojo_report" in result["content"][0]["text"]


def test_attribution_tool_serves_the_shadow_account_report(tmp_path: Path) -> None:
    _seed_attribution(tmp_path)
    result = _call(tmp_path, "spy_der_attribution")
    assert result["isError"] is False
    body = result["structuredContent"]
    assert body["verdict"] == "execution_drag"
    assert body["components"]["entry"] == "-100.0000"


def test_report_index_respects_the_limit(tmp_path: Path) -> None:
    # Stamped names are second-resolution, so distinct `now` values are what
    # make these three separate files rather than three writes of one.
    for hour, day in enumerate(("2026-07-22", "2026-07-23", "2026-07-24")):
        persist_dojo_report(
            tmp_path / "reports" / "dojo",
            report_date=day,
            summary=day,
            flags=[],
            metrics={},
            now=datetime(2026, 7, 24, 10 + hour, 0, tzinfo=UTC),
        )
    result = _call(tmp_path, "spy_der_dojo_reports", {"limit": 2})
    assert len(result["structuredContent"]["reports"]) == 2


def test_absurd_limit_is_clamped_not_rejected(tmp_path: Path) -> None:
    result = _call(tmp_path, "spy_der_dojo_reports", {"limit": 10_000})
    assert result["isError"] is False


def test_non_numeric_limit_falls_back_to_the_default(tmp_path: Path) -> None:
    result = _call(tmp_path, "spy_der_dojo_reports", {"limit": "lots"})
    assert result["isError"] is False


def test_unknown_tool_is_a_tool_error(tmp_path: Path) -> None:
    result = _call(tmp_path, "spy_der_launch_missiles")
    assert result["isError"] is True
    assert "unknown tool" in result["content"][0]["text"]


def test_tools_call_without_a_name_is_an_invalid_request(tmp_path: Path) -> None:
    response = _rpc(tmp_path, "tools/call", {"arguments": {}})
    assert response["error"]["code"] == -32600


def test_unreadable_state_names_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recurring production failure is state written 0600 by another unit.

    Injected rather than provoked with chmod: the suite may run as root, where
    a 0000 file is still readable and the test would silently pass for the
    wrong reason.
    """

    def _denied(self: DashboardApiState) -> dict[str, Any] | None:
        raise PermissionError("Permission denied: latest.json")

    monkeypatch.setattr(DashboardApiState, "latest_dojo_report", _denied)
    result = _call(tmp_path, "spy_der_dojo_latest")
    assert result["isError"] is True
    assert "permission" in result["content"][0]["text"].lower()
    assert "state root" in result["content"][0]["text"]


# --------------------------------------------------------------------------- #
# stdio loop                                                                  #
# --------------------------------------------------------------------------- #
def test_serve_round_trips_newline_delimited_json(tmp_path: Path) -> None:
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        + "\n"
    )
    stdout = io.StringIO()
    assert serve(tmp_path, stdin=stdin, stdout=stdout) == 0
    lines = [json.loads(line) for line in stdout.getvalue().splitlines() if line]
    # The notification must not produce a response.
    assert [line["id"] for line in lines] == [1, 2]
    assert len(lines[1]["result"]["tools"]) == len(TOOLS)


def test_serve_survives_malformed_input(tmp_path: Path) -> None:
    stdin = io.StringIO(
        "not json\n"
        + json.dumps([1, 2, 3])
        + "\n"
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"})
        + "\n"
    )
    stdout = io.StringIO()
    assert serve(tmp_path, stdin=stdin, stdout=stdout) == 0
    lines = [json.loads(line) for line in stdout.getvalue().splitlines() if line]
    assert lines[0]["error"]["code"] == -32700
    assert lines[1]["error"]["code"] == -32600
    # A bad line must not stop the session.
    assert lines[-1]["id"] == 9
