# Apply SPY-DER parallel panel to 0DTE

This agent could not push to `DGator86/0DTE` (403). The dashboard/VPS wiring
lives in `integrations/zerodte/0dte-spy-der-parallel-panel.patch`.

## Apply on a 0DTE checkout

```bash
cd /path/to/0DTE
git checkout main && git pull
git checkout -b cursor/spy-der-parallel-panel
git am /path/to/SPY-DER/integrations/zerodte/0dte-spy-der-parallel-panel.patch
git push -u origin cursor/spy-der-parallel-panel
```

Then open a PR on 0DTE. VPS install steps: `docs/ops/SPY_DER_PARALLEL.md`.
