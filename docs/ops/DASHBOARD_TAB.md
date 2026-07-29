# SPY-DER dashboard tab

**Learning and Dojo are on SPY-DER.** The operator surface is
`spy-der-dashboard-api` at `GET /ui`. 0DTE may embed the same assets during
migration; it does not own Learning/Dojo.

```
src/spy_der/runtime/ui/          the tab (index.html, spy-der-tab.js, .css)
        |
        +-- spy-der-dashboard-api  GET /ui        primary on the VPS
        +-- spy-der Vercel project /              public SPY-DER-owned page
        +-- 0DTE Vercel dashboard  a tab pane      optional embed during migration
```

Public hosting: [`VERCEL_DASHBOARD.md`](VERCEL_DASHBOARD.md) (`vercel.json` +
`/api` proxy → `SPY_DER_DASHBOARD_URL`).

The assets ship inside the installed package (`[tool.setuptools.package-data]`),
so the systemd unit reads them out of the venv rather than depending on a
checkout being present on the VPS.

## Primary: SPY-DER `/ui`

```bash
sudo systemctl enable --now spy-der-dashboard-api.service
# on the VPS, or via `ssh -L 8788:127.0.0.1:8788 …`
open http://127.0.0.1:8788/ui
```

Same origin as `/v1/*`, so no proxy and no `data-spy-der-base` prefix.

The service stays read-only: `deploy/spy-der-dashboard-api.service` sets
`ReadOnlyPaths=/var/lib/spy-der`, and `/ui` only reads files inside the
installed package directory.

## Public: spy-der Vercel project

See [`VERCEL_DASHBOARD.md`](VERCEL_DASHBOARD.md). After `vercel.json` lands on
`main` and `SPY_DER_DASHBOARD_URL` points at a tunnel to `:8788`, open the
`spy-der` project URL — no 0DTE involvement.

## Optional: embed in 0DTE

See [`integrations/zerodte/spy_der_tab/README.md`](../../integrations/zerodte/spy_der_tab/README.md)
and the ready Vercel patch
[`integrations/zerodte/README-adaptive-loop-vercel.md`](../../integrations/zerodte/README-adaptive-loop-vercel.md).

Summary: serve the two assets, replace Learning/Dojo with an Adaptive Loop
container carrying `data-spy-der-tab` + `data-spy-der-actions`, and proxy
`/api/spy-der/v1/*`. Prefer the `spy-der` Vercel project or VPS `/ui` unless
you specifically need the legacy Command Center host.

## What it shows

| Panel | Source | Notes |
| --- | --- | --- |
| Decision chain | `/v1/state` | candidates → deterministic risk → authority → guard → executor |
| Current decision | `/v1/state` | confidence, uncertainty, size scalar, reason codes, models, rationale |
| Shadow account | `/v1/attribution/latest` | model book vs actual book, gap decomposition, behavioural flags |
| System health | `/v1/system` | services, feed, AI gate, deploy |
| Learning · Dojo | `/v1/dojo/progress`, `/v1/dojo/latest`, `/v1/validation/latest` | live working square, finished report, parity |
| Open positions | `/v1/state` | live positions with unrealized P&L |

`/v1/state` carries either published shape — a `spyder.dashboard.v1` packet from
the decision service, or a `spy_der.parallel.v1` heartbeat from the VPS runner,
whose decision fields live under `parallel`. The tab reads both, because a
heartbeat is a legitimate live state and rendering "unknown" against one would
be wrong.

### Decision-chain fidelity

The chain panel renders what the published packet actually carries. Stages the
packet does not carry yet — candidate counts entering and leaving eligibility,
and whether the guard shrank a specific decision or passed it through — are
drawn as "not published" rather than inferred. Inventing them would misrepresent
exactly the property the panel exists to demonstrate.

Publishing `eligible_count`, `considered_count` and a guard verdict on
`DashboardPacket` would let the panel show the real funnel. That is a contracts
change, so it is deliberately not bundled here.

## Why not fork a dashboard

Evaluated and rejected in
[`docs/VIBE_TRADING_EVALUATION.md`](../VIBE_TRADING_EVALUATION.md). Short
version: the dashboard was the one genuinely missing piece, and it is also the
cheapest piece to write. Importing a platform to get it would have added a
second deployment surface above a system that spent seventeen migration phases
escaping exactly that shape of dependency.
