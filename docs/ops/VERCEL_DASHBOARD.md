# SPY-DER on its own Vercel page

The Adaptive Loop · Dojo dashboard at **[spy-der.vercel.app](https://spy-der.vercel.app)**
([project](https://vercel.com/darrins-projects-5d4fb02f/spy-der)), independent of
the legacy `0-dte` Command Center.

```
Browser  →  spy-der.vercel.app        static shell + /ui/spy-der-tab.{js,css}
         →  /api/v1/*                 rewritten to api/proxy.js
         →  native  SPY_DER_DASHBOARD_URL → 127.0.0.1:8788   (full /v1 surface)
            bridge  0DTE host /api/spy-der + /api/system      (read-only subset)
```

This is **not** a Python serverless app. The first deploy failed with
`No python entrypoint found` because Vercel auto-detected `pyproject.toml`.
Root [`vercel.json`](../../vercel.json) sets `"framework": null` so the project
builds as static + Node instead.

## It works with no configuration

There is nothing to set for the page to show live data. With
`SPY_DER_DASHBOARD_URL` unset, `api/proxy.js` runs in **bridge** mode and reads
SPY-DER state through the 0DTE host, which publishes it from the same files on
the same box. The decision, health and Dojo panels are real.

Bridge mode cannot serve what that host does not publish: validation reports,
the attribution waterfall, live Dojo progress, pending challengers, and operator
Promote / Reject / Rollback. Those panels say so rather than rendering empty.

Set `SPY_DER_DASHBOARD_URL` to upgrade to **native** mode and get all of it.

## Routing: why the rewrite exists

`vercel.json` rewrites `/api/:path*` to `/api/proxy?__path=:path*`.

That rule is load-bearing. The proxy was originally a zero-config catch-all at
`api/[...path].js`, which in production matched only a **single** segment:
`/api/health` reached the function, `/api/v1/system` returned Vercel's own 404
before the function ran. Every endpoint the tab reads is nested under `/v1`, so
the entire page was dark while the deployment looked healthy — build green,
function compiled, static assets served. `0-dte` carries the same file and never
hit it, because all of its endpoints are single-segment (`/api/system`,
`/api/dojo`).

If `/api/v1/system` ever 404s with `x-vercel-error: NOT_FOUND`, the rewrite is
missing — the function is not being reached at all.

## Upgrading to a direct connection

`spy-der-dashboard-api` binds **loopback only** (`127.0.0.1:8788`); neither the
browser nor Vercel can reach that address. Expose it, on the VPS:

```bash
cloudflared tunnel --url http://127.0.0.1:8788    # or Tailscale Funnel, Caddy, …
```

Then in Project → Settings → Environment Variables (Production + Preview):

| Name | Required | Value |
| --- | --- | --- |
| `SPY_DER_DASHBOARD_URL` | no — unset means bridge | Reachable base of the dashboard API, no trailing slash |
| `DASHBOARD_TOKEN` | only behind a hop that wants `Authorization` | Bearer token that hop expects |
| `SPY_DER_DASHBOARD_MODE` | no | Force `native` or `bridge` instead of inferring from the URL |

Redeploy is not required — the next request picks the value up.

Two values that will **not** work:

- `http://127.0.0.1:8788` — that is the serverless function's own loopback, not the VPS.
- `https://spy-der.vercel.app` — the page itself. The proxy would call its own
  rewrite until the invocation times out, so it is rejected with an explanatory 500.

A URL ending in `/api/spy-der` is read as the 0DTE hop and handled in bridge
mode. That hop is a **read-only adapter over published files** — it is not a
proxy to `:8788`, so it cannot carry operator writes no matter how it is
addressed.

### Operator Promote / Reject

Native mode only. On the VPS:

```bash
sudoedit /etc/spy-der/spy-der.env   # SPY_DER_OPERATOR_TOKEN=...
sudo systemctl restart spy-der-dashboard-api
```

On the page: Unlock → paste the same token. It travels as
`X-Spy-Der-Operator-Token`, separate from `DASHBOARD_TOKEN`, and reaches only
decision knobs — it cannot place, size or submit a trade. The execution guard
remains the only route to the market.

## Checking it

`/api/__status` reports which mode the deployment resolved, and the host it
reads from — never the token. The page's own connection chip is built on it.

```bash
curl -s https://spy-der.vercel.app/api/__status | jq
curl -s https://spy-der.vercel.app/api/v1/system | jq '.overall, .source'
curl -s https://spy-der.vercel.app/api/v1/state  | jq '.action, .schema_version'
```

Locally:

```bash
bash scripts/vercel-build.sh   # emits public/{index.html,favicon.svg,ui/*}
python -m pytest tests/unit/test_vercel_dashboard.py
```

| Symptom | Cause |
| --- | --- |
| `404 NOT_FOUND` on `/api/v1/*` | rewrite missing from `vercel.json` |
| `503` naming `SPY_DER_DASHBOARD_URL` | native mode with an unreachable upstream |
| `500` about proxying to itself | `SPY_DER_DASHBOARD_URL` set to this page |
| Panels say "not published through the 0DTE bridge" | expected in bridge mode — set a tunnel |

## Layout

| Path | Role |
| --- | --- |
| `web/index.html` | Shell for **this host**: top bar, connection chip, `data-spy-der-base="/api"` |
| `web/favicon.svg` | Tab icon |
| `api/proxy.js` | Gateway — native passthrough or bridge translation |
| `scripts/vercel-build.sh` | Assembles `public/` from `web/` + `src/spy_der/runtime/ui/` |

`web/index.html` is a separate file from the canonical
[`src/spy_der/runtime/ui/index.html`](../../src/spy_der/runtime/ui/index.html)
because the hosts genuinely differ: that one is served by
`spy-der-dashboard-api` same-origin with `/v1/*`, this one is static and has to
name its upstream. The build used to derive one from the other by string
replacement, which produced a silently wrong page whenever the canonical
comment was reworded.

The tab itself is **not** forked. `spy-der-tab.js` and `.css` are copied
byte-for-byte from `src/spy_der/runtime/ui/`, and a test asserts it.

## Relation to 0DTE

| Surface | Role |
| --- | --- |
| `http://127.0.0.1:8788/ui` | Primary operator surface on the VPS |
| **`spy-der` Vercel project** | Public SPY-DER-owned page (this doc) |
| `0-dte` Vercel + Adaptive Loop patch | Optional embed during migration; not required once `spy-der` is live |

Bridge mode is a migration convenience and the last runtime dependency this page
has on 0DTE. Standing up the tunnel removes it.
