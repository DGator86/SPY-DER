"""Guardrails for the standalone spy-der Vercel dashboard deploy."""

from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_vercel_json_forces_other_framework_not_python() -> None:
    """pyproject.toml alone makes Vercel pick Python and fail with no entrypoint."""
    cfg = json.loads((_ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert cfg.get("framework") is None
    assert cfg.get("outputDirectory") == "public"
    assert "vercel-build.sh" in str(cfg.get("buildCommand", ""))


def test_vercel_proxy_and_build_script_exist() -> None:
    assert (_ROOT / "api" / "[...path].js").is_file()
    assert (_ROOT / "scripts" / "vercel-build.sh").is_file()
    assert (_ROOT / ".env.vercel.example").is_file()
    assert (_ROOT / "docs" / "ops" / "VERCEL_DASHBOARD.md").is_file()


def test_vercel_build_emits_api_base_shell() -> None:
    import shutil
    import subprocess

    script = _ROOT / "scripts" / "vercel-build.sh"
    public = _ROOT / "public"
    if public.exists():
        shutil.rmtree(public)

    subprocess.run(["bash", str(script)], check=True, cwd=_ROOT)
    try:
        index = (public / "index.html").read_text(encoding="utf-8")
        assert 'data-spy-der-base="/api"' in index
        assert (public / "ui" / "spy-der-tab.js").is_file()
        assert (public / "ui" / "spy-der-tab.css").is_file()
        proxy = (_ROOT / "api" / "[...path].js").read_text(encoding="utf-8")
        assert "SPY_DER_DASHBOARD_URL" in proxy
        assert "promote|reject|rollback" in proxy
    finally:
        if public.exists():
            shutil.rmtree(public)
