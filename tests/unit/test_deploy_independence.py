"""SPY-DER's deployment has no runtime dependency on 0DTE.

`docs/CUTOVER_PLAN.md` states that after final cutover there is no runtime
reference to `/opt/zerodte`, `/var/lib/zerodte` or `/etc/zerodte`. A statement in
a document is not a guarantee, so these tests are the guarantee: they scan the
shipped units, the env/config templates and the package source, and fail on any
0DTE path or unit name.

The deliberate exception is `spy_der/integrations/zerodte/`, the temporary
compatibility surface that PR #150's bridge speaks. It is re-export-only and is
deleted at cutover step 10; a test asserts it stays that way rather than growing
logic.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY = _ROOT / "deploy"
_SRC = _ROOT / "src" / "spy_der"

#: Filesystem paths that must never appear in a shipped unit or template.
FORBIDDEN_PATHS = ("/opt/zerodte", "/var/lib/zerodte", "/etc/zerodte")

#: The 10 units the target deployment requires.
REQUIRED_UNITS = (
    "spy-der-market.service",
    "spy-der-engine.service",
    "spy-der-agent.service",
    "spy-der-settlement.service",
    "spy-der-dashboard-api.service",
    "spy-der-dojo-daily.timer",
    "spy-der-dojo-recent.timer",
    "spy-der-dojo-weekly.timer",
    "spy-der-validation-daily.timer",
    "spy-der-validation-weekly.timer",
)

#: State subdirectories the runtime owns under /var/lib/spy-der.
REQUIRED_STATE_DIRS = (
    "market",
    "chains",
    "bars",
    "forecasts",
    "candidates",
    "decisions",
    "positions",
    "settlements",
    "journal",
    "reports",
    "memories",
    "lessons",
    "configs",
    "usage",
)


def _deploy_files() -> list[Path]:
    return sorted(p for p in _DEPLOY.iterdir() if p.is_file())


# --------------------------------------------------------------------------- #
# No 0DTE paths anywhere in deployment                                        #
# --------------------------------------------------------------------------- #
def test_no_deploy_file_references_a_zerodte_path() -> None:
    offenders: list[str] = []
    for path in _deploy_files():
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_PATHS:
            if forbidden in text:
                offenders.append(f"{path.name}: {forbidden}")
    assert not offenders, offenders


def test_no_deploy_file_declares_a_zerodte_unit_or_user() -> None:
    """A `zerodte-*` unit, or a service running as the zerodte user, is a leak."""
    offenders: list[str] = []
    for path in _deploy_files():
        if path.name.startswith("zerodte"):
            offenders.append(f"unit file named {path.name}")
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "zerodte" in stripped:
                offenders.append(f"{path.name}: {stripped}")
    assert not offenders, offenders


def test_package_source_references_no_zerodte_filesystem_path() -> None:
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_PATHS:
            if forbidden in text:
                offenders.append(f"{path.relative_to(_ROOT)}: {forbidden}")
    assert not offenders, offenders


# --------------------------------------------------------------------------- #
# The target service set is complete                                          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("unit", REQUIRED_UNITS)
def test_required_unit_is_shipped(unit: str) -> None:
    assert (_DEPLOY / unit).is_file(), f"missing {unit}"


@pytest.mark.parametrize("unit", [u for u in REQUIRED_UNITS if u.endswith(".timer")])
def test_every_timer_has_a_matching_service(unit: str) -> None:
    service = unit.removesuffix(".timer") + ".service"
    assert (_DEPLOY / service).is_file(), f"{unit} has no {service}"


@pytest.mark.parametrize("unit", [u for u in REQUIRED_UNITS if u.endswith(".service")])
def test_every_service_runs_as_the_spy_der_user(unit: str) -> None:
    text = (_DEPLOY / unit).read_text(encoding="utf-8")
    assert "User=spy-der" in text, unit
    assert "Group=spy-der" in text, unit


@pytest.mark.parametrize("unit", [u for u in REQUIRED_UNITS if u.endswith(".service")])
def test_every_service_is_hardened_and_state_scoped(unit: str) -> None:
    text = (_DEPLOY / unit).read_text(encoding="utf-8")
    assert "NoNewPrivileges=true" in text, unit
    assert "StateDirectory=spy-der" in text, unit
    assert "SPY_DER_STATE_ROOT=%S/spy-der" in text, unit


def test_dashboard_api_serves_the_dojo_report_the_timers_write() -> None:
    """The reports directory the dojo units write must be the one the API reads."""
    dojo = (_DEPLOY / "spy-der-dojo-daily.service").read_text(encoding="utf-8")
    assert "--reports-dir /var/lib/spy-der/reports/dojo" in dojo
    api = (_DEPLOY / "spy-der-dashboard-api.service").read_text(encoding="utf-8")
    assert "--state-root %S/spy-der" in api


def test_dashboard_api_cannot_mutate_state() -> None:
    """A read-only data service must not be able to write runtime state."""
    text = (_DEPLOY / "spy-der-dashboard-api.service").read_text(encoding="utf-8")
    assert "ReadOnlyPaths=/var/lib/spy-der" in text
    assert "ReadWritePaths=/var/lib/spy-der" not in text


def test_dashboard_api_binds_loopback_only() -> None:
    text = (_DEPLOY / "spy-der-dashboard-api.service").read_text(encoding="utf-8")
    assert "--host 127.0.0.1" in text
    assert "0.0.0.0" not in text


def test_deterministic_engine_has_no_network() -> None:
    """The engine stage is reproducible from a snapshot; it needs no network."""
    text = (_DEPLOY / "spy-der-engine.service").read_text(encoding="utf-8")
    assert "PrivateNetwork=true" in text


def test_no_unit_enables_live_trading() -> None:
    for path in _deploy_files():
        text = path.read_text(encoding="utf-8")
        assert "SPY_DER_ALLOW_LIVE=1" not in text, path.name
        assert "--live-trading" not in text, path.name


# --------------------------------------------------------------------------- #
# Shipped units invoke commands that actually exist                           #
# --------------------------------------------------------------------------- #
#: Subcommands named by a shipped unit that the CLI does not implement yet.
#: `spy-der <cmd>` exits 2 for these, so the unit fails on every start — a
#: oneshot dies immediately and a `Restart=always` service crash-loops. Keeping
#: them here makes the gap visible instead of silent; delete a name when the
#: command lands. `dashboard-api` was on this list until it was implemented, and
#: its absence is why a completed Dojo run never surfaced a report.
PENDING_CLI_COMMANDS = frozenset({"engine", "settlement", "validate"})


def _cli_commands() -> set[str]:
    """Command names `spy_der.cli.main` dispatches, read from its source.

    Parsed rather than executed: importing is cheap but dispatch lives in
    `if cmd in {...}` branches, and the branch bodies import heavy runtime
    modules. The set literals are the authoritative list.
    """
    source = (_SRC / "cli.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_SRC / "cli.py"))
    commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, ast.In) for op in node.ops):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "cmd"):
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Set):
                for element in comparator.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        commands.add(element.value)
    return commands


def _exec_start_subcommand(text: str) -> str | None:
    """The `spy-der <subcommand>` a unit runs, across line continuations."""
    joined = text.replace("\\\n", " ")
    for line in joined.splitlines():
        stripped = line.strip()
        if not stripped.startswith("ExecStart="):
            continue
        tokens = stripped.removeprefix("ExecStart=").split()
        if not tokens:
            return None
        # tokens[0] is the venv binary; the subcommand is the first bare word.
        for token in tokens[1:]:
            if not token.startswith("-"):
                return token
        return None
    return None


@pytest.mark.parametrize(
    "unit", sorted(p.name for p in _DEPLOY.glob("*.service"))
)
def test_every_shipped_unit_invokes_a_real_cli_command(unit: str) -> None:
    """A unit whose ExecStart names a nonexistent command cannot ever run.

    `spy-der-dashboard-api.service` shipped pointing at a `dashboard-api`
    command that did not exist, so nothing ever served the Dojo reports the
    timers were faithfully writing.
    """
    path = _DEPLOY / unit
    subcommand = _exec_start_subcommand(path.read_text(encoding="utf-8"))
    assert subcommand, f"{unit} has no parseable ExecStart subcommand"
    if subcommand in PENDING_CLI_COMMANDS:
        pytest.xfail(f"{subcommand} is a known-pending CLI command")
    assert subcommand in _cli_commands(), (
        f"{unit} runs `spy-der {subcommand}`, which spy_der.cli.main does not "
        f"dispatch — the unit will exit 2 on every start"
    )


def test_pending_command_list_is_accurate() -> None:
    """A command that has landed must be removed from the pending list."""
    implemented = _cli_commands() & PENDING_CLI_COMMANDS
    assert not implemented, (
        f"{sorted(implemented)} are implemented — drop them from PENDING_CLI_COMMANDS"
    )


# --------------------------------------------------------------------------- #
# Configuration templates                                                     #
# --------------------------------------------------------------------------- #
def test_env_template_exists_and_carries_no_secret_values() -> None:
    path = _DEPLOY / "spy-der.env.example"
    assert path.is_file()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if any(token in key for token in ("KEY", "PASSWORD", "SECRET", "TOKEN")):
            assert value == "", f"{key} must ship empty, got {value!r}"


def test_env_template_defaults_to_shadow_with_live_disabled() -> None:
    text = (_DEPLOY / "spy-der.env.example").read_text(encoding="utf-8")
    assert "SPY_DER_EXECUTION_MODE=shadow" in text
    assert "SPY_DER_ALLOW_LIVE=0" in text


def test_config_template_declares_every_state_directory() -> None:
    text = (_DEPLOY / "config.yaml.example").read_text(encoding="utf-8")
    for name in REQUIRED_STATE_DIRS:
        assert f"- {name}" in text, f"state directory {name} not declared"


def test_config_template_declares_parity_tolerances() -> None:
    """The cutover plan requires tolerances to be explicit, not implied."""
    text = (_DEPLOY / "config.yaml.example").read_text(encoding="utf-8")
    assert "tolerances:" in text
    # Quantities that must match exactly carry no tolerance.
    for exact in ("maximum_loss", "hard_vetoes", "settlement", "candidate_ids"):
        assert exact in text
    assert "maximum_loss_abs" not in text
    assert "hard_vetoes_abs" not in text


def test_config_template_does_not_auto_promote() -> None:
    text = (_DEPLOY / "config.yaml.example").read_text(encoding="utf-8")
    assert "auto_promote: false" in text


# --------------------------------------------------------------------------- #
# The bridge stays a shim                                                     #
# --------------------------------------------------------------------------- #
def test_zerodte_integration_is_re_export_only() -> None:
    """The compatibility surface must not grow logic — it is deleted at cutover.

    Only re-exports are allowed: imports, ``__all__``, and the module docstring.
    A function or class defined here would be SPY-DER logic filed under the name
    of the system being retired.
    """
    package = _SRC / "integrations" / "zerodte"
    init = package / "__init__.py"
    tree = ast.parse(init.read_text(encoding="utf-8"), filename=str(init))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # docstring
        if isinstance(node, ast.Assign) and all(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            continue
        raise AssertionError(
            f"{init.relative_to(_ROOT)} defines {type(node).__name__} — the 0DTE "
            "compatibility surface must be re-export only"
        )


def test_decision_authority_lives_under_spy_der_decisions() -> None:
    """The production decision path is SPY-DER's, not an integration detail."""
    assert (_SRC / "decisions" / "shadow.py").is_file()
    assert not (_SRC / "integrations" / "zerodte" / "provider.py").exists()


def test_dojo_does_not_depend_on_the_zerodte_integration_for_universes() -> None:
    text = (_SRC / "dojo" / "universe.py").read_text(encoding="utf-8")
    assert "spy_der.synthetic" in text
    assert "integrations.zerodte" not in text
