# SPY-DER Dojo

> Migrated ownership: the Dojo belongs to SPY-DER, not 0DTE.
> See `docs/OWNERSHIP_BOUNDARY.md` and `docs/DOJO_MIGRATION.md`.

## What it is (read this first)

The Vercel **Dojo** tab shows SPY-DER Dojo reports. Dojo is a **nightly exam +
study plan**, not an open-ended trainer:

| Question | Answer |
|---|---|
| What data? | **Stored real sessions** (`inbox/experience`) plus **synthetic stress worlds**. Not the live market feed. |
| Does it trade live? | **No.** Live knobs change only after a validated promotion writes `champion.json`. |
| Why stop before “great”? | Fixed timer budgets (e.g. daily: 6 worlds × 1 generation). Weak market types raise weights for the **next** generation / next night — the run does not loop until every archetype is green. |

Each report includes a `human` block (`headline`, `data_story`, `stop_reason`,
`next_step`) for dashboards. The Vercel tab rewrite lives in
[`integrations/zerodte/dojo-tab-human-ui.patch`](../../integrations/zerodte/dojo-tab-human-ui.patch).

The Dojo compresses market experience into one run:

1. **recorded** — walk `MarketExperienceProvider`; score champion / challenger / baseline via `CandidateEvaluator`
2. **sequential** — leak-free blind-day forward transfer + retention panel
3. **learner** — diagnose → hypothesize → optimize (holdout) → stage a challenger only if gates pass
4. **universe** — spar against `SyntheticUniverseProvider` packets with the same AI scoring path
5. **promotion** — re-run 1 and 2 with the staged change installed as the candidate champion, and write `champion.json` if it wins

## Promotion

A recommendation does not promote anything. When the learner stages a
challenger, the Dojo re-runs the system with that change installed and promotes
it only if the re-run beats the incumbent on **every** gate:

| Gate | Passes when |
|---|---|
| `actionable` | the change touches a live decision knob (`risk_max_size_scalar`, `min_confidence`, `prefer_abstain_on_ood`) |
| `evidence` | the candidate re-run scored ≥ 20 matched trades over ≥ 3 sessions |
| `pnl_edge` | candidate total P&L beats the incumbent's on the same tape |
| `win_rate` | and gives back ≤ 0.05 of win rate doing it |
| `forward_transfer` | mean forward transfer ≥ 0 on leak-free blind days |
| `retention` | no forgetting regression on the retention panel |
| `universe` | the synthetic panel does not disagree |
| `cooldown` | ≥ 6h since the last automatic promotion (three timers fire daily) |

A promotion writes `configs/champion.json` with the validation report attached,
snapshots the outgoing config into `configs/champion_history/`, and moves the
staged file to `configs/promoted/`. The live decision service reads those knobs
on the next tick, so a promotion changes decisions rather than only paperwork.
Knobs can only reduce exposure — the size scalar is a cap, never a lift.

```bash
# stop promoting (staging still happens)
SPY_DER_DOJO_AUTO_PROMOTE=0 venv/bin/spy-der dojo ...     # or --no-auto-promote
# make the live path ignore a promoted config without deleting the audit trail
SPY_DER_CHAMPION_KNOBS=0
# put the previous champion back
venv/bin/python -c "from spy_der.learning.promotion import rollback_champion; \
    print(rollback_champion('/var/lib/spy-der/configs'))"
```

The human path is still there: `promote_pending(configs, candidate_id,
human_ack="PROMOTE")` promotes a staged candidate directly.

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

## Preconditions

If recorded tape is below `min_sessions`, the **universe lattice is refused**
(`status=skipped`, flag `universe_skipped_no_tape`). A full-lattice weekend run
with zero sessions previously burned ~an hour generating snapshots and scored
none of them. Override only when you explicitly want synthetic-only sparring:

```bash
venv/bin/spy-der dojo --force-universe --full-lattice ...
```

Native synthetic outcomes now carry per-candidate terminal P&L against the
world's settlement, so when the lattice *does* run it is scored.

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

## Gap-driven sparring

Universe sparring builds a per-archetype robustness matrix (P&L / win rate by
market type). After each generation the Dojo re-weights the catalog toward the
weakest and least-visited archetypes (`spy_der.synthetic.evolution`), so the
next draws spend time where the champion is worst — that is the point of the
Dojo.

- **Intra-run:** with `--generations N` (N > 1), generation *k+1* samples from
  weights evolved from generation *k*'s **local** scores (not the cumulative
  matrix). Coverage/unvisited cells stay cumulative across the run.
- **Curriculum inertia:** each new plan blends with the prior
  (`w = (1-a)*w_hat + a*w_prior`, a ≈ 0.35) so a large measurement pass cannot
  erase accumulated gap pressure.
- **Across runs:** the final plan is written to
  `configs/curriculum_weights.json` and loaded as the seed weights of the next
  Dojo run.
- **Weekly full lattice:** generation 0 still enumerates every cell
  (measurement — sampling weights do not apply). The plan after that
  measurement still blends in the prior curriculum; generations ≥ 1 then
  sample with those blended weights.

The report’s `metrics.phases.universe.remediation` block lists focus
archetypes ranked by the evolution plan, each with reasons (negative P&L,
low directional accuracy, unvisited regimes, prior curriculum carry, …).

## Reports

A run writes a stamped report plus a `latest.json` pointer:

```
/var/lib/spy-der/reports/dojo/dojo_YYYYMMDD_HHMMSS.json
/var/lib/spy-der/reports/dojo/latest.json
/var/lib/spy-der/configs/curriculum_weights.json
```

Both report files are published world-readable (0644, minus the operator umask)
because the dashboard API and the 0DTE adapter read them as different users.

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
