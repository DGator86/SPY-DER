# Human Dojo tab for the Vercel (0DTE) dashboard

The Vercel **Dojo** tab *is* the SPY-DER Dojo — it reads
`/var/lib/spy-der/reports/dojo/latest.json`. The jargon you saw
(`DecisionAuthority`, `LATTICE`, file paths, `promotion_pending_review`) came
from 0DTE’s renderer in `dashboard/static/app.js`, not from a separate product.

SPY-DER now attaches a plain-English `human` block to every report. This patch
rewrites the Vercel Dojo tab to use it and to answer, on every run:

1. **What this checked** — stored real sessions + synthetic stress worlds  
2. **Data used** — not live trading  
3. **Why it stopped** — fixed nightly budget; it does not grind until “great”  
4. **Tonight’s focus** — which weak market types get more practice next  

## Apply on 0DTE

From a checkout of `DGator86/0DTE`:

```bash
git apply /path/to/SPY-DER/integrations/zerodte/dojo-tab-human-ui.patch
# or:
patch -p1 < /path/to/SPY-DER/integrations/zerodte/dojo-tab-human-ui.patch
```

Files touched:

- `dashboard/static/app.js` — human `renderDojoDetail` / list / matrix  
- `dashboard/static/index.html` — intro copy  
- `dashboard/static/style.css` — story / focus styles  

Deploy the Vercel dashboard as usual after merging.

## Without the patch

Even before the UI patch lands, new SPY-DER Dojo runs write `human.headline`,
`human.data_story`, and `human.stop_reason` into `latest.json`, and strip
evaluator class names from phase `note` fields. The list summary line will
already read better once the VPS is on a SPY-DER build that includes this
change.
