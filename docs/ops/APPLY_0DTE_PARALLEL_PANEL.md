# Apply SPY-DER parallel panel to 0DTE (then VPS auto-deploys)

The VPS `zerodte-update.timer` pulls **0DTE `main`** every ~2 minutes and runs
`remote-deploy.sh`. That deploy now also updates `/opt/spy-der` and installs it
into the 0DTE venv.

This agent **cannot push to `DGator86/0DTE`** (GitHub 403). You only need to
land the patch on 0DTE `main`; the scanner does the rest.

## One-time: merge into 0DTE

```bash
cd /path/to/0DTE
git checkout main && git pull
git checkout -b cursor/spy-der-parallel-panel
git am /path/to/SPY-DER/integrations/zerodte/0dte-spy-der-parallel-panel.patch
git push -u origin HEAD
# open PR -> merge to main
```

Within ~2 minutes of merge, the VPS should:

1. Pull the new 0DTE commit
2. Clone/update `/opt/spy-der` from SPY-DER `main`
3. `pip install -e /opt/spy-der`
4. Restart shadow + dashboard

## Optional

Add `XAI_API_KEY=...` to `/etc/zerodte/zerodte.env` for Grok decisions.
Without it, SPY-DER still appears using the deterministic agent.

See `docs/ops/SPY_DER_PARALLEL.md`.
