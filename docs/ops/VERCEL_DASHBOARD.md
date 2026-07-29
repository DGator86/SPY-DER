# SPY-DER on its own Vercel page

Host the Adaptive Loop · Dojo dashboard at the **`spy-der`** Vercel project
([vercel.com/.../spy-der](https://vercel.com/darrins-projects-5d4fb02f/spy-der)),
independent of the legacy `0-dte` Command Center.

```
Browser  →  spy-der.vercel.app          (static / + /ui/*)
         →  /api/v1/*                   (Node serverless proxy)
         →  SPY_DER_DASHBOARD_URL       (tunnel / reverse proxy)
         →  127.0.0.1:8788              (spy-der-dashboard-api on the VPS)
```

This is **not** a Python serverless app. The failed deploy
(`No python entrypoint found`) happened because Vercel auto-detected
`pyproject.toml`. Root [`vercel.json`](../../vercel.json) sets
`"framework": null` so the project builds as static + Node instead.

## One-time setup

### 1. Merge this repo config

Ship on `main`:

- `vercel.json` — framework Other, build → `public/`
- `api/[...path].js` — proxy
- `scripts/vercel-build.sh` — copies `src/spy_der/runtime/ui/` into `public/`
- `.env.vercel.example` — env template

After merge, Vercel rebuilds from GitHub. Confirm Framework Preset is
**Other** (Project → Settings → General). `vercel.json` overrides it; if an
old Python setting sticks, set Other manually once.

### 2. Expose the VPS API to Vercel

`spy-der-dashboard-api` binds **loopback only** (`127.0.0.1:8788`). The
browser and Vercel cannot reach that address directly. Pick one:

| Approach | `SPY_DER_DASHBOARD_URL` | Notes |
| --- | --- | --- |
| Dedicated tunnel (preferred) | `https://<your-tunnel-host>` | Cloudflare Tunnel, Tailscale Funnel, Caddy, etc. forward to `127.0.0.1:8788` |
| Temporary via 0DTE hop | `https://<vps-public>/api/spy-der` | Requires `DASHBOARD_TOKEN`; keep until a SPY-DER-only tunnel exists |

Do **not** set the URL to `http://127.0.0.1:8788` in Vercel — that is the
function's own loopback, not the VPS.

### 3. Set Vercel env vars

Project → Settings → Environment Variables (Production + Preview):

| Name | Required | Value |
| --- | --- | --- |
| `SPY_DER_DASHBOARD_URL` | yes | Reachable base of the dashboard API (no trailing slash) |
| `DASHBOARD_TOKEN` | only for 0DTE hop | Bearer token that hop expects |

Redeploy after saving.

### 4. Operator Promote / Reject

On the VPS:

```bash
sudoedit /etc/spy-der/spy-der.env   # SPY_DER_OPERATOR_TOKEN=...
sudo systemctl restart spy-der-dashboard-api
```

On the Vercel page: Unlock → paste the same token. It travels as
`X-Spy-Der-Operator-Token` (never places trades — decision knobs only).

## Local check before relying on Vercel

```bash
bash scripts/vercel-build.sh
# public/index.html should contain data-spy-der-base="/api"
# public/ui/spy-der-tab.js and .css should exist

# On the VPS / via SSH tunnel:
spy-der-dashboard-api --state-root /var/lib/spy-der --port 8788
open http://127.0.0.1:8788/ui
```

If `/ui` looks right locally, a blank Vercel page is almost always env or
tunnel wiring — not the tab itself.

## Relation to 0DTE

| Surface | Role |
| --- | --- |
| `http://127.0.0.1:8788/ui` | Primary operator surface on the VPS |
| **`spy-der` Vercel project** | Public SPY-DER-owned page (this doc) |
| `0-dte` Vercel + Adaptive Loop patch | Optional embed during migration; not required once `spy-der` is live |

Canonical assets stay in [`src/spy_der/runtime/ui/`](../../src/spy_der/runtime/ui/).
There is one implementation; Vercel only vendors a build-time copy.
