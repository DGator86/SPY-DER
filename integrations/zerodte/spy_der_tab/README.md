# SPY-DER tab for the 0DTE Vercel dashboard

Adds a dedicated **SPY-DER** tab beside the existing Legacy / V2 / V3 views:
decision chain, current decision, shadow-account attribution, system health,
Dojo and parity, open positions.

This directory contains **no code**. The tab lives in SPY-DER, at
`src/spy_der/runtime/ui/`, and ships inside the installed package. 0DTE mounts
it; it does not own a copy. That matters for two reasons:

- There is one implementation. A fix to the tab is a SPY-DER commit, and the VPS
  deploy already fast-forwards `/opt/spy-der` and reinstalls it — no second
  patch to land on 0DTE.
- It survives cutover. `spy-der-dashboard-api` serves the same asset at `/ui`,
  so when 0DTE is retired the tab keeps working with nothing to port.

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

### 2. Add the tab button and container

In `dashboard/static/index.html`, alongside the existing tab buttons and panes:

```html
<button class="tab-button" data-tab="spy-der">SPY-DER</button>

<section id="tab-spy-der" class="tab-pane">
  <div data-spy-der-tab data-spy-der-base="/api/spy-der"></div>
</section>

<link rel="stylesheet" href="/static/spy-der-tab.css" />
<script type="module" src="/static/spy-der-tab.js"></script>
```

`data-spy-der-base` is the prefix for the five reads below. Drop the attribute
entirely when the dashboard is served from the same origin as
`spy-der-dashboard-api`.

If the dashboard creates panes lazily, add `data-spy-der-manual` to the
container and call `mountSpyDerTab({ target })` when the tab is first shown:

```js
import { mountSpyDerTab } from "/static/spy-der-tab.js";
mountSpyDerTab({ target: document.querySelector("[data-spy-der-tab]") });
```

### 3. Proxy five read-only endpoints

The browser cannot reach `127.0.0.1:8788` directly, so 0DTE proxies. Each route
is a straight pass-through of one `spy-der-dashboard-api` GET:

| 0DTE route                       | SPY-DER API              |
| -------------------------------- | ------------------------ |
| `/api/spy-der/v1/system`         | `/v1/system`             |
| `/api/spy-der/v1/state`          | `/v1/state`              |
| `/api/spy-der/v1/dojo/latest`    | `/v1/dojo/latest`        |
| `/api/spy-der/v1/validation/latest` | `/v1/validation/latest` |
| `/api/spy-der/v1/attribution/latest` | `/v1/attribution/latest` |

Forward the upstream status code unchanged. **404 is meaningful** — it is how
the tab distinguishes "no Dojo run has completed" from "the read failed", and
rewriting it to 200 with an empty body makes the tab report absence as data.

If proxying is awkward, 0DTE may instead read the same files directly, as
`docs/ops/0DTE_DASHBOARD_ADAPTER.md` already specifies, and serve them under
those paths. The tab does not care which, as long as the shapes and status codes
are preserved.

## Verifying before touching 0DTE

The standalone mount renders the identical tab with no 0DTE involved:

```bash
spy-der-dashboard-api --state-root /var/lib/spy-der --port 8788
# then open http://127.0.0.1:8788/ui
```

If it looks right there, the embed is a plumbing problem, not a tab problem.

## Guarantees

- **Read-only.** The tab issues GETs and renders. No code path in it can
  submit, size, approve or promote anything. Deterministic risk and
  `spy_der.execution.guard` remain the only route to a trade.
- **Style-isolated.** Every CSS rule is scoped under `.spyder-tab`, and the
  custom properties are declared there rather than on `:root`, so nothing
  restyles the host page.
- **Injection-safe.** Server values are written with `textContent`, never
  `innerHTML`, so a rationale or reason code cannot inject markup into the 0DTE
  page.
- **Independently degrading.** Each panel fetches on its own. A missing report
  greys out one panel with a reason; it never blanks the tab.
