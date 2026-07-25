# SPY-DER Dojo

> Migrated ownership: the Dojo belongs to SPY-DER, not 0DTE.
> See `docs/OWNERSHIP_BOUNDARY.md` and `docs/DOJO_MIGRATION.md`.

The Dojo compresses market experience into one run:

1. **recorded** — walk `MarketExperienceProvider`; score champion / challenger / baseline via `CandidateEvaluator`
2. **sequential** — leak-free blind-day forward transfer + retention panel
3. **learner** — diagnose → hypothesize → optimize (holdout) → stage `pending_review` only if gates pass
4. **universe** — spar against `SyntheticUniverseProvider` packets with the same AI scoring path

It never writes `champion.json`. Promotion is human-gated.

## VPS quick start

```bash
cd /opt/spy-der
venv/bin/spy-der dojo \
    --reports-dir /var/lib/spy-der/reports/dojo \
    --configs-dir /var/lib/spy-der/configs \
    --experience-dir /var/lib/spy-der/inbox/experience \
    --recent-days 3 \
    --days 3 \
    --universes 6 \
    --generations 1 \
    --trials 10
```

## Timers

| Timer | When (ET) | Window |
|---|---|---|
| `spy-der-dojo-daily` | Mon–Fri 06:30 | last 3 days |
| `spy-der-dojo-recent` | Mon/Wed/Fri 07:00 | last 10 days |
| `spy-der-dojo-weekly` | Sat 15:00 | full lattice |

```bash
sudo cp deploy/spy-der-dojo-{daily,recent,weekly}.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now spy-der-dojo-daily.timer \
                            spy-der-dojo-recent.timer \
                            spy-der-dojo-weekly.timer
```

## Reports

A run writes a stamped report plus a `latest.json` pointer:

```
/var/lib/spy-der/reports/dojo/dojo_YYYYMMDD_HHMMSS.json
/var/lib/spy-der/reports/dojo/latest.json
```

Both are published world-readable (0644, minus the operator umask) because the
dashboard API and the 0DTE adapter read them as different users.

## Serving the report

`spy-der-dashboard-api.service` exposes the report read-only on loopback:

```bash
sudo cp deploy/spy-der-dashboard-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now spy-der-dashboard-api.service
```

| Route | Returns |
|---|---|
| `GET /health` | liveness + resolved state root |
| `GET /v1/dojo/latest` | newest Dojo report |
| `GET /v1/dojo/reports?limit=N` | index of stamped reports, newest first |
| `GET /v1/validation/latest` | newest parity-validation report |
| `GET /v1/validation/reports?limit=N` | index of stamped validation reports |
| `GET /v1/state` | `live_state.json` (`spyder.dashboard.v1`) |

```bash
curl -s http://127.0.0.1:8788/v1/dojo/latest | jq '.summary, .flags'
```

The 0DTE dashboard adapter reads `latest.json` (or `live_state.json`) through
the `spyder.dashboard.v1` contract only.

## Troubleshooting: a run finished but no report is visible

Work down the chain — each step tells you which link is broken.

1. **Did the timer fire?**

   ```bash
   systemctl list-timers 'spy-der-dojo-*'
   journalctl -u spy-der-dojo-daily.service --since '2 days ago'
   ```

   The `OnCalendar=... America/New_York` timezone suffix needs **systemd 252+**
   (`systemd-analyze --version`). On older systemd the timer silently fails to
   parse and never fires; drop the suffix and use a UTC time instead.

2. **Did the run write a file?**

   ```bash
   ls -l /var/lib/spy-der/reports/dojo/
   ```

3. **Can the reader open it?** A `latest.json` at mode `0600` is invisible to
   every consumer but `spy-der`. Files written before this was fixed keep the
   old mode until the next run overwrites them:

   ```bash
   sudo chmod 0644 /var/lib/spy-der/reports/dojo/*.json
   ```

4. **Is anything serving it?**

   ```bash
   systemctl status spy-der-dashboard-api.service
   curl -s http://127.0.0.1:8788/health
   ```

   `unknown command: dashboard-api` in the journal means the deployed venv
   predates the `dashboard-api` command — redeploy and
   `sudo systemctl restart spy-der-dashboard-api.service`.
