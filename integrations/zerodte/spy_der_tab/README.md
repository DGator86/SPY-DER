# SPY-DER tab for the 0DTE Vercel dashboard

Optional embed of the SPY-DER tab as the **Adaptive Loop** surface during
migration.

**Learning and Dojo are owned by SPY-DER.** Prefer
`http://127.0.0.1:8788/ui` on the VPS (or an SSH tunnel to it). This 0DTE
mount is convenience only — it does not own Learning/Dojo.

Ready-made Vercel patch (owner push required):
[`../README-adaptive-loop-vercel.md`](../README-adaptive-loop-vercel.md).

This directory contains **no code**. The tab lives in SPY-DER, at
`src/spy_der/runtime/ui/`, and ships inside the installed package. 0DTE mounts
it; it does not own a copy. That matters for two reasons:

- There is one implementation. A fix to the tab is a SPY-DER commit, and the VPS
  deploy already fast-forwards `/opt/spy-der` and reinstalls it — no second
  patch to land on 0DTE.
- The primary surface is already `spy-der-dashboard-api` at `/ui`; when 0DTE is
  retired, nothing Learning/Dojo-related needs to move.

The existing `../0dte-spy-der-parallel-panel.patch` is unrelated and stays as
is: it adds SPY-DER as a fourth *card* in the Parallel decisions panel. This is
a full tab, and the two can coexist.

## What 0DTE has to add

**No JavaScript edit.** The module auto-mounts onto any element carrying
`data-spy-der-tab`, so the host page never calls into it and cannot conflict
with `dashboard/static/app.js`.

### 1. Serve the two assets

They are installed with the `spy_der` package, already present in the 0DTE venv.
Resolve them from the package rather than hardcoding a site-packages path:

```python
# dashboard/spy_der_assets.py
from pathlib import Path

import spy_der.runtime

SPY_DER_UI = Path(spy_der.runtime.__file__).resolve().parent / "ui"
```

Then expose `SPY_DER_UI / "spy-der-tab.js"` at `/static/spy-der-tab.js` and
`SPY_DER_UI / "spy-der-tab.css"` at `/static/spy-der-tab.css`, using whatever
static-file mechanism the dashboard already uses. Serve the `.js` as
`text/javascript` — a wrong content type makes the browser refuse the module.

For **Vercel**, also vendor the two files into `dashboard/static/` (see the
ready patch) so the CDN build does not need the Python package.

### 2. Add the Adaptive Loop tab and container

In `dashboard/static/index.html`, replace the Learning / Dojo panes with:

```html
<button class="tab" data-tab="dojo">Adaptive Loop</button>

<section id="tab-dojo" class="tab-pane">
  <div
    data-spy-der-tab
    data-spy-der-base="/api/spy-der"
    data-spy-der-actions
  ></div>
</section>

<link rel="stylesheet" href="/static/spy-der-tab.css" />
<script type="module" src="/static/spy-der-tab.js"></script>
```

`data-spy-der-base` is the prefix for the reads below. Drop the attribute
entirely when the dashboard is served from the same origin as
`spy-der-dashboard-api`.

`data-spy-der-actions` unlocks Promote / Reject / Rollback for **decision
knobs** (not forecast models). The browser pastes `SPY_DER_OPERATOR_TOKEN`
into the panel; it never places trades.

If the dashboard creates panes lazily, add `data-spy-der-manual` to the
container and call `mountSpyDerTab({ target })` when the tab is first shown:

```js
import { mountSpyDerTab } from "/static/spy-der-tab.js";
mountSpyDerTab({ target: document.querySelector("[data-spy-der-tab]"), actions: true });
```

### 3. Proxy the SPY-DER API routes

The browser cannot reach `127.0.0.1:8788` directly, so 0DTE proxies. Prefer a
catch-all pass-through:

| 0DTE route | SPY-DER API |
| --- | --- |
| `/api/spy-der/v1/system` | `/v1/system` |
| `/api/spy-der/v1/state` | `/v1/state` |
| `/api/spy-der/v1/dojo/progress` | `/v1/dojo/progress` |
| `/api/spy-der/v1/dojo/latest` | `/v1/dojo/latest` |
| `/api/spy-der/v1/dojo/pending` | `/v1/dojo/pending` |
| `/api/spy-der/v1/dojo/promote` (POST) | `/v1/dojo/promote` |
| `/api/spy-der/v1/dojo/reject` (POST) | `/v1/dojo/reject` |
| `/api/spy-der/v1/dojo/rollback` (POST) | `/v1/dojo/rollback` |
| `/api/spy-der/v1/validation/latest` | `/v1/validation/latest` |
| `/api/spy-der/v1/attribution/latest` | `/v1/attribution/latest` |

Forward the upstream status code unchanged. **404 is meaningful** on GETs.
Operator POSTs require `X-Spy-Der-Operator-Token` when the Vercel hop rewrites
`Authorization` to `DASHBOARD_TOKEN`.

## Verifying before touching 0DTE

The standalone mount renders the identical tab with no 0DTE involved:

```bash
spy-der-dashboard-api --state-root /var/lib/spy-der --port 8788
# then open http://127.0.0.1:8788/ui
```

If it looks right there, the embed is a plumbing problem, not a tab problem.

## Guarantees

- **No live trading from the browser.** Promote / Reject only mutate
  `configs/champion.json` (decision knobs). Deterministic risk and
  `spy_der.execution.guard` remain the only route to a trade.
- **Style-isolated.** Every CSS rule is scoped under `.spyder-tab`, and the
  custom properties are declared there rather than on `:root`, so nothing
  restyles the host page.
- **Injection-safe.** Server values are written with `textContent`, never
  `innerHTML`, so a rationale or reason code cannot inject markup into the 0DTE
  page.
- **Independently degrading.** Each panel fetches on its own. A missing report
  greys out one panel with a reason; it never blanks the tab.
