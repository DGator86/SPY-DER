#!/usr/bin/env bash
# Build the static Adaptive Loop shell for the spy-der Vercel project.
# Copies canonical UI assets from the package source and rewrites the
# standalone index so API calls go through /api → api/[...path].js.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UI_SRC="${ROOT}/src/spy_der/runtime/ui"
OUT="${ROOT}/public"

if [[ ! -f "${UI_SRC}/spy-der-tab.js" || ! -f "${UI_SRC}/spy-der-tab.css" || ! -f "${UI_SRC}/index.html" ]]; then
  echo "vercel-build: missing UI assets under ${UI_SRC}" >&2
  exit 1
fi

rm -rf "${OUT}"
mkdir -p "${OUT}/ui"
cp "${UI_SRC}/spy-der-tab.js" "${OUT}/ui/spy-der-tab.js"
cp "${UI_SRC}/spy-der-tab.css" "${OUT}/ui/spy-der-tab.css"

python3 - "${UI_SRC}/index.html" "${OUT}/index.html" <<'PY'
"""Emit the Vercel shell: same assets, API via /api proxy."""
from __future__ import annotations

import sys
from pathlib import Path

src = Path(sys.argv[1]).read_text(encoding="utf-8")
# Point the tab at the Node proxy. Canonical /ui is same-origin with /v1;
# on Vercel the browser cannot reach the VPS loopback.
if 'data-spy-der-base=' not in src:
    src = src.replace(
        'data-spy-der-tab data-spy-der-actions',
        'data-spy-der-tab data-spy-der-base="/api" data-spy-der-actions',
        1,
    )
src = src.replace(
    "Same-origin: the API serving this page also serves /v1/*, so the\n"
    "         default endpoints need no base prefix.",
    "Vercel shell: data-spy-der-base=/api routes reads and operator POSTs\n"
    "         through api/[...path].js to SPY_DER_DASHBOARD_URL on the VPS.",
    1,
)
Path(sys.argv[2]).write_text(src, encoding="utf-8")
print(f"wrote {sys.argv[2]}")
PY

echo "vercel-build: public/ ready (index.html + ui/spy-der-tab.{js,css})"
