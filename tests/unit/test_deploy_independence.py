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
#: command lands. Every shipped unit now names a real command, so the set is
#: empty — it stays as the guard for the next unit that ships ahead of its
#: implementation. `dashboard-api`'s absence is why a completed Dojo run never
#: surfaced a report.
PENDING_CLI_COMMANDS: frozenset[str] = frozenset()


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


#: The console script every runtime unit invokes.
_SPY_DER_BIN = "/opt/spy-der/venv/bin/spy-der"


def _exec_start_tokens(text: str) -> list[str]:
    """ExecStart's argv, joined across line continuations."""
    joined = text.replace("\\\n", " ")
    for line in joined.splitlines():
        stripped = line.strip()
        if stripped.startswith("ExecStart="):
            return stripped.removeprefix("ExecStart=").split()
    return []


def _exec_start_subcommand(text: str) -> str | None:
    """The `spy-der <subcommand>` a unit runs, or None if it runs something else.

    Returning None for a non-`spy-der` ExecStart matters: `spy-der-update.service`
    legitimately runs a shell script, and treating its first bare word (`bash`)
    as a CLI subcommand would fail the audit for a unit that is entirely correct.
    Those units are covered by `test_units_that_run_a_script_ship_that_script`
    instead, so nothing goes unchecked.
    """
    tokens = _exec_start_tokens(text)
    if not tokens or tokens[0] != _SPY_DER_BIN:
        return None
    for token in tokens[1:]:
        if not token.startswith("-"):
            return token
    return None


def _units_invoking_the_cli() -> list[str]:
    return sorted(
        p.name
        for p in _DEPLOY.glob("*.service")
        if _exec_start_tokens(p.read_text(encoding="utf-8"))[:1] == [_SPY_DER_BIN]
    )


@pytest.mark.parametrize("unit", _units_invoking_the_cli())
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


def test_every_runtime_unit_is_audited_by_the_cli_check() -> None:
    """Guard the guard: only the self-update unit may sidestep the CLI audit.

    If a future unit invokes a different binary, this fails and forces a
    decision rather than letting it slip past the audit unnoticed.
    """
    audited = set(_units_invoking_the_cli())
    everything = {p.name for p in _DEPLOY.glob("*.service")}
    assert everything - audited == {"spy-der-update.service"}


@pytest.mark.parametrize(
    "unit",
    sorted(
        p.name
        for p in _DEPLOY.glob("*.service")
        if _exec_start_tokens(p.read_text(encoding="utf-8"))[:1] != [_SPY_DER_BIN]
    ),
)
def test_units_that_run_a_script_ship_that_script(unit: str) -> None:
    """A unit pointing at a deploy script must point at one that exists.

    The same class of defect as the missing `dashboard-api` command: a unit
    referencing a path the repo does not contain fails on every start.
    """
    tokens = _exec_start_tokens((_DEPLOY / unit).read_text(encoding="utf-8"))
    scripts = [t for t in tokens if t.endswith(".sh")]
    assert scripts, f"{unit} runs neither the CLI nor a shipped script"
    for script in scripts:
        name = Path(script).name
        assert (_DEPLOY / name).is_file(), f"{unit} runs {script}, not shipped in deploy/"


def test_pending_command_list_is_accurate() -> None:
    """A command that has landed must be removed from the pending list."""
    implemented = _cli_commands() & PENDING_CLI_COMMANDS
    assert not implemented, (
        f"{sorted(implemented)} are implemented — drop them from PENDING_CLI_COMMANDS"
    )


# --------------------------------------------------------------------------- #
# The deploy path ships the code it claims to                                 #
# --------------------------------------------------------------------------- #
def _remote_deploy() -> str:
    return (_DEPLOY / "remote-deploy.sh").read_text(encoding="utf-8")


def test_deploy_scripts_are_shipped_and_executable() -> None:
    for name in ("remote-deploy.sh", "self-update.sh"):
        path = _DEPLOY / name
        assert path.is_file(), name
        assert path.stat().st_mode & 0o111, f"{name} is not executable"


def test_remote_deploy_installs_every_required_unit() -> None:
    """A unit absent from the deploy script never reaches systemd.

    Every `spy-der-*.service` and timer in this repo used to be installed by
    nothing at all — they existed in `deploy/` and were never copied to
    `/etc/systemd/system`, so they could not run however correct they were.
    """
    text = _remote_deploy()
    for unit in REQUIRED_UNITS:
        stem = unit.removesuffix(".service").removesuffix(".timer")
        assert stem in text, f"{unit} is never installed by remote-deploy.sh"


def test_remote_deploy_installs_the_package_into_the_venv() -> None:
    """The step whose absence stranded `spy-der dashboard-api` after it existed.

    A `git reset --hard` alone moves source but never creates new console-script
    entry points or installs new dependencies.
    """
    text = _remote_deploy()
    assert "pip" in text and "install" in text
    assert "venv/bin/pip" in text


def test_remote_deploy_creates_every_declared_state_directory() -> None:
    text = _remote_deploy()
    for name in REQUIRED_STATE_DIRS:
        assert name in text, f"state directory {name} is never created"


def test_remote_deploy_owns_state_as_the_service_user() -> None:
    """Ownership must match `StateDirectory=spy-der`, or the two flap.

    systemd resets `/var/lib/spy-der` to the unit's user on start, so a deploy
    that chowns it to anyone else just loses the next time a unit starts.
    """
    text = _remote_deploy()
    assert 'SVC_USER=spy-der' in text
    assert 'chown -R "$SVC_USER:$SVC_USER" "$STATE_DIR"' in text


def test_remote_deploy_leaves_state_directories_traversable() -> None:
    """Published reports are read by other local users; 0700 would hide them."""
    assert "chmod 0755" in _remote_deploy()


def test_remote_deploy_repairs_legacy_unreadable_reports() -> None:
    """Reports written before the 0644 fix stay 0600 until overwritten.

    Without this the operator has to know to chmod them by hand, and until they
    do, a report that exists on disk reads as a Dojo that never ran.
    """
    text = _remote_deploy()
    assert "chmod 0644" in text
    assert "reports" in text


def test_remote_deploy_never_starts_units_without_the_secrets_file() -> None:
    text = _remote_deploy()
    assert "/etc/spy-der/spy-der.env" in text
    assert "not found" in text


def test_remote_deploy_does_not_write_the_secrets_file() -> None:
    """A deploy that can overwrite the env file can destroy a key."""
    for line in _remote_deploy().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "ENV_FILE" in stripped and ("install " in stripped or "cp " in stripped):
            raise AssertionError(f"deploy writes the secrets file: {stripped}")


def test_self_update_runs_the_fetched_deploy_script_not_the_stale_one() -> None:
    """Unit-file and dependency changes must ship with the code needing them."""
    text = (_DEPLOY / "self-update.sh").read_text(encoding="utf-8")
    assert "git -C \"$APP_DIR\" show" in text
    assert "deploy/remote-deploy.sh" in text


def test_self_update_is_a_noop_when_already_current() -> None:
    """A 2-minute timer must stay silent, or it floods the journal."""
    text = (_DEPLOY / "self-update.sh").read_text(encoding="utf-8")
    assert 'if [ "$local_sha" = "$remote_sha" ]' in text


def test_update_timer_polls_on_a_bounded_interval() -> None:
    text = (_DEPLOY / "spy-der-update.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=" in text
    assert "OnBootSec=" in text


def test_remote_deploy_enables_the_self_update_timer() -> None:
    """Otherwise the first deploy is also the last automatic one."""
    text = _remote_deploy()
    assert "enable --now spy-der-update.timer" in text


def test_deploy_targets_this_repo() -> None:
    text = _remote_deploy()
    assert "SPY-DER.git" in text


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


def test_config_template_declares_promotion_gates() -> None:
    """Automatic promotion is fine; unbounded automatic promotion is not.

    The template must show the operator the bars a challenger clears before the
    Dojo writes champion.json, so 'auto_promote: true' is never the whole story.
    """
    text = (_DEPLOY / "config.yaml.example").read_text(encoding="utf-8")
    assert "auto_promote: true" in text
    for gate in (
        "promote_min_trades",
        "promote_min_sessions",
        "promote_min_pnl_edge",
        "promote_max_win_rate_drop",
        "promote_cooldown_hours",
    ):
        assert gate in text, f"promotion gate {gate} not declared"


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
