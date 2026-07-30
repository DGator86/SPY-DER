"""Guardrails for the standalone spy-der Vercel dashboard deploy."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _vercel_config() -> dict:
    return json.loads((_ROOT / "vercel.json").read_text(encoding="utf-8"))


def test_vercel_json_forces_other_framework_not_python() -> None:
    """pyproject.toml alone makes Vercel pick Python and fail with no entrypoint."""
    cfg = _vercel_config()
    assert cfg.get("framework") is None
    assert cfg.get("outputDirectory") == "public"
    assert "vercel-build.sh" in str(cfg.get("buildCommand", ""))


def test_api_requests_are_rewritten_to_the_proxy() -> None:
    """Every endpoint the tab reads is nested (`/api/v1/...`).

    Zero-config `api/[...path].js` matched only a single segment in production:
    `/api/health` reached the function while `/api/v1/system` returned Vercel's
    own 404, so the whole page rendered empty. The rewrite makes the mapping
    explicit; without it the dashboard is silently dark.
    """
    rewrites = _vercel_config().get("rewrites") or []
    assert rewrites, "no rewrites — nested /api/v1/* paths will not reach the proxy"
    rule = rewrites[0]
    assert rule["source"] == "/api/:path*"
    assert rule["destination"].startswith("/api/proxy")
    assert "__path" in rule["destination"], "proxy needs the tail to forward upstream"


def test_catch_all_proxy_is_gone() -> None:
    """Leaving it behind would shadow the rewrite with the broken routing."""
    assert not (_ROOT / "api" / "[...path].js").exists()


def test_vercel_sources_exist() -> None:
    assert (_ROOT / "api" / "proxy.js").is_file()
    assert (_ROOT / "web" / "index.html").is_file()
    assert (_ROOT / "web" / "favicon.svg").is_file()
    assert (_ROOT / "scripts" / "vercel-build.sh").is_file()
    assert (_ROOT / ".env.vercel.example").is_file()
    assert (_ROOT / "docs" / "ops" / "VERCEL_DASHBOARD.md").is_file()


def test_proxy_serves_both_modes() -> None:
    """The page must render without a tunnel and upgrade itself once one exists."""
    proxy = (_ROOT / "api" / "proxy.js").read_text(encoding="utf-8")
    assert "SPY_DER_DASHBOARD_URL" in proxy
    assert "promote|reject|rollback" in proxy
    # Bridge fallback: SPY-DER state stays readable through the 0DTE host while
    # spy-der-dashboard-api has no tunnel of its own.
    assert "v1/dojo/latest" in proxy
    assert "0-dte-kappa.vercel.app" in proxy
    # A URL pointing at this page would make the function call its own rewrite.
    assert "proxy to itself" in proxy


def test_proxy_recovers_the_request_tail_three_ways() -> None:
    """Which form the tail arrives in is a routing-layer detail, not a promise.

    The rewrite token may fail to expand, `req.url` may carry either the
    original path or the rewritten destination, and Vercel auto-appends source
    params the destination path does not consume. Reading only one of those is
    what left every /v1 endpoint 404ing, so all three are tried and an
    unrecoverable tail fails loudly instead of querying the wrong upstream path.
    """
    proxy = (_ROOT / "api" / "proxy.js").read_text(encoding="utf-8")
    assert '__path' in proxy
    assert 'requestUrl.pathname' in proxy
    assert 'params.get("path")' in proxy
    assert 'did not carry the request path' in proxy


def test_tab_assets_are_vendored_not_forked() -> None:
    """One implementation. The build copies; it must not keep an edited copy."""
    for name in ("spy-der-tab.js", "spy-der-tab.css"):
        assert (_ROOT / "src" / "spy_der" / "runtime" / "ui" / name).is_file()
        assert not (_ROOT / "web" / name).exists(), f"web/{name} would drift from source"


@pytest.fixture()
def built_public() -> Path:
    public = _ROOT / "public"
    if public.exists():
        shutil.rmtree(public)
    subprocess.run(["bash", str(_ROOT / "scripts" / "vercel-build.sh")], check=True, cwd=_ROOT)
    try:
        yield public
    finally:
        if public.exists():
            shutil.rmtree(public)


def test_vercel_build_emits_shell_and_assets(built_public: Path) -> None:
    index = (built_public / "index.html").read_text(encoding="utf-8")
    # Without this the tab requests /v1/* against the static host and gets nothing.
    assert 'data-spy-der-base="/api"' in index
    assert "data-spy-der-tab" in index
    assert (built_public / "ui" / "spy-der-tab.js").is_file()
    assert (built_public / "ui" / "spy-der-tab.css").is_file()
    assert (built_public / "favicon.svg").is_file()


def test_vendored_tab_is_byte_identical_to_source(built_public: Path) -> None:
    ui_src = _ROOT / "src" / "spy_der" / "runtime" / "ui"
    for name in ("spy-der-tab.js", "spy-der-tab.css"):
        assert (built_public / "ui" / name).read_bytes() == (ui_src / name).read_bytes()


def test_vercel_build_fails_when_shell_loses_api_base(tmp_path: Path) -> None:
    """The build asserts the one thing that makes this host different."""
    sandbox = tmp_path / "repo"
    shutil.copytree(
        _ROOT,
        sandbox,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "public", "__pycache__", "*.pyc", ".mypy_cache", ".pytest_cache"
        ),
    )
    shell = sandbox / "web" / "index.html"
    shell.write_text(
        shell.read_text(encoding="utf-8").replace('data-spy-der-base="/api"', ""),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(sandbox / "scripts" / "vercel-build.sh")],
        cwd=sandbox,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "data-spy-der-base" in result.stderr
