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

Reports:

```
/var/lib/spy-der/reports/dojo/latest.json
```

The 0DTE dashboard adapter reads that file (or `live_state.json`) through the
`spyder.dashboard.v1` contract only.
