#!/usr/bin/env bash
# Assemble public/ for the spy-der Vercel project.
#
# Two sources, deliberately:
#   web/                     — chrome that only this host needs (top bar,
#                              connection indicator, /api base).
#   src/spy_der/runtime/ui/  — the dashboard itself, vendored unchanged so
#                              there is one implementation and no copy to drift.
#
# This used to derive index.html from the canonical shell with string
# replacement. That silently produced the wrong page whenever the canonical
# comment was reworded, so the shell is now an explicit file and the copy is
# verified below instead of assumed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UI_SRC="${ROOT}/src/spy_der/runtime/ui"
WEB_SRC="${ROOT}/web"
OUT="${ROOT}/public"

for asset in "${UI_SRC}/spy-der-tab.js" "${UI_SRC}/spy-der-tab.css" \
             "${WEB_SRC}/index.html" "${WEB_SRC}/favicon.svg"; do
  if [[ ! -f "${asset}" ]]; then
    echo "vercel-build: missing required asset ${asset}" >&2
    exit 1
  fi
done

rm -rf "${OUT}"
mkdir -p "${OUT}/ui"

cp "${UI_SRC}/spy-der-tab.js" "${OUT}/ui/spy-der-tab.js"
cp "${UI_SRC}/spy-der-tab.css" "${OUT}/ui/spy-der-tab.css"
cp "${WEB_SRC}/index.html" "${OUT}/index.html"
cp "${WEB_SRC}/favicon.svg" "${OUT}/favicon.svg"

# The one thing that makes this host different from the VPS shell. If it is
# missing, every panel silently requests the wrong origin and the page is blank.
if ! grep -q 'data-spy-der-base="/api"' "${OUT}/index.html"; then
  echo "vercel-build: web/index.html must mount the tab with data-spy-der-base=\"/api\"" >&2
  exit 1
fi

echo "vercel-build: public/ ready (index.html + favicon.svg + ui/spy-der-tab.{js,css})"
