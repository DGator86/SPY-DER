# Push Adaptive Loop embed to 0DTE (Vercel dashboard)

`cursor[bot]` **cannot push** to `DGator86/0DTE` (GitHub 403). This package
ships a ready `git am` patch so the owner can land the Vercel dashboard update
in one step.

## What the patch does

1. Replaces the Learning + Dojo tabs with one **Adaptive Loop** tab that mounts
   `spy-der-tab.js` (same assets as SPY-DER `/ui`).
2. Vendors `spy-der-tab.js` / `.css` into `dashboard/static/` for Vercel.
3. Proxies `/api/spy-der/v1/*` on the VPS dashboard to
   `spy-der-dashboard-api` (`SPY_DER_DASHBOARD_URL`, default `http://127.0.0.1:8788`).
4. Allows operator POSTs for promote / reject / rollback through the otherwise
   read-only middleware and the Vercel serverless proxy
   (`X-Spy-Der-Operator-Token`).

## Apply

```bash
cd /path/to/0DTE
git checkout main && git pull
git checkout -b cursor/adaptive-loop-embed-f51d
git am /path/to/SPY-DER/integrations/zerodte/ready/0001-embed-spy-der-adaptive-loop-on-vercel.patch
git push -u origin HEAD
# open PR → merge to main
```

Windows: `integrations/zerodte/PUSH_ADAPTIVE_LOOP_TO_0DTE.cmd` (set `0DTE=`).

## After merge

1. Vercel redeploys `0-dte` from `main`.
2. VPS `zerodte-update.timer` pulls the proxy within ~2 minutes.
3. On the VPS:

```bash
# enable Promote / Reject (decision knobs only)
sudoedit /etc/spy-der/spy-der.env   # add SPY_DER_OPERATOR_TOKEN=...
sudo systemctl restart spy-der-dashboard-api
```

4. Open the Adaptive Loop tab → Unlock with the operator token → Promote/Reject.
