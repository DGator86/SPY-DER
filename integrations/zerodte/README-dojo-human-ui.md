# Human Dojo tab for the Vercel (0DTE) dashboard

The Vercel **Dojo** tab *is* the SPY-DER Dojo — it reads
`/var/lib/spy-der/reports/dojo/latest.json`.

SPY-DER attaches a plain-English `human` block to every report. The phone UI
must answer, on every run:

1. **What this checked** — stored real sessions + synthetic stress worlds  
2. **Data used** — not live trading  
3. **Why it stopped** — fixed nightly budget; it does not grind until “great”  
4. **Next** — which weak market types get more practice  

## Apply on 0DTE

Preferred (current production UI):

```bash
# From DGator86/0DTE at main
patch -p0 -d dashboard/static < /path/to/SPY-DER/integrations/zerodte/dojo-tab-purpose-story.patch
# Also update the Dojo intro in dashboard/static/index.html to the nightly-exam copy
# (see dojo-purpose-story-0dte.patch for the full 3-file change).
```

Full commit-style patch:

```bash
git am /path/to/SPY-DER/integrations/zerodte/dojo-purpose-story-0dte.patch
# or:
git apply /path/to/SPY-DER/integrations/zerodte/dojo-purpose-story-0dte.patch
```

Older wholesale rewrite (superseded by the purpose-story patch on top of the
current narrative UI):

- `dojo-tab-human-ui.patch`
- `dojo_tab_human.js.snippet`

## Without the UI patch

New SPY-DER Dojo runs already write `human.headline`, `human.data_story`,
`human.stop_reason`, and strip evaluator class names from phase notes. The
list summary line reads in plain English once the VPS is on a build that
includes that change.
