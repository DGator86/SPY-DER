# SPY-DER parallel track on the VPS

SPY-DER appears beside Legacy / V2 / V3 in the dashboard **Parallel decisions**
panel and as a fourth independent paper ledger (`spy_der`).

## Install on the VPS

```bash
# Same venv the shadow runner uses
sudo -u zerodte /opt/zerodte/venv/bin/pip install -e /opt/spy-der

# Optional: standalone heartbeat publisher
sudo cp /opt/spy-der/deploy/spy-der-shadow.service /etc/systemd/system/
sudo systemctl enable --now spy-der-shadow.service
```

Clone / sync SPY-DER to `/opt/spy-der` (or your preferred path) and keep it
updated with `main`.

## Environment

Add to `/etc/zerodte/zerodte.env` when you want live Grok decisions:

```bash
XAI_API_KEY=...
# optional model override is owned by SPY-DER GrokConfig / deployment manifest
```

Without `XAI_API_KEY`, SPY-DER falls back to the deterministic ensemble agent
so the parallel panel still updates every tick.

## What you should see

1. **Parallel decisions** panel — four cards: Legacy, V2, V3, SPY-DER.
2. **Paper** metrics — `SPY-DER P&L` beside the other tracks.
3. Open positions tagged `fill_track=spy_der`.

Live broker routing remains disabled. This is paper/shadow comparison only.

## Rollback

```bash
# Disable SPY-DER package import (track shows UNAVAILABLE)
sudo -u zerodte /opt/zerodte/venv/bin/pip uninstall -y spy-der

# Or roll the 0DTE checkout back before the parallel-track PR
```
