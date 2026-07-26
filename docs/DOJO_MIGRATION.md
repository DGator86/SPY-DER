# Dojo Migration Plan

Migrate Dojo, Sequential Dojo, adaptive learning, and AI training schedules from
`DGator86/0DTE` into `DGator86/SPY-DER` without copying 0DTE market-simulation
internals.

See `docs/OWNERSHIP_BOUNDARY.md` for the ownership rule.

## Coupling to remove

The 0DTE Dojo currently imports:

- `backtest.run_backtest`
- `journal.Journal`
- `matrix_universe` (catalog / Markov feeds)
- `walk_forward`
- `adaptive_learning.learner`

It also runs from `/opt/zerodte` and writes under `/var/lib/zerodte/reports/dojo`.
That is the coupling this migration removes.

## Phases

### Phase 1 — Contracts (this PR)

Define versioned packets and experience-provider protocols so neither repo
reaches into the other’s internals.

### Phase 2 — Decision process boundary

Replace in-process `spy_der_bridge` → `decide_shadow_tick` with a local HTTP
service:

```
0DTE → POST localhost:8787/v1/decision → SPY-DER
```

Filesystem inbox/outbox remains available as a fallback for non-latency paths.

### Phase 3 — Move Dojo units

| From (0DTE) | To (SPY-DER) |
|---|---|
| `dojo.py` | `src/spy_der/dojo/runner.py` |
| `sequential_dojo.py` | `src/spy_der/dojo/sequential.py` |
| `docs/dojo.md` | `docs/ops/dojo.md` |
| `docs/sequential_dojo.md` | `docs/ops/sequential_dojo.md` |
| `deploy/zerodte-dojo-*` | `deploy/spy-der-dojo-*` |
| `/opt/zerodte` cwd | `/opt/spy-der` |
| `/var/lib/zerodte/reports/dojo` | `/var/lib/spy-der/reports/dojo` |
| `/var/lib/zerodte/configs` | `/var/lib/spy-der/configs` |
| `spy_der_state.json` | `/var/lib/spy-der/live_state.json` |

### Phase 4 — Decouple evaluation primitives

**Stay in / reimplement inside SPY-DER**

- AI walk-forward scoring, champion/challenger, forward transfer, forgetting
- Lesson extraction, promotion gates, Dojo reports, curriculum scheduling

**Consumed from 0DTE via providers**

- Recorded market snapshots / candidates / settlements
- Synthetic market snapshots
- Backtest execution against 0DTE strategies

Protocols:

- `MarketExperienceProvider`
- `SyntheticUniverseProvider`
- `CandidateEvaluator`

Initial provider implementations may read 0DTE recorded files / SQLite. Ownership
is the interface and call direction, not immediate physical data relocation.

### Phase 5 — Adaptive learning

Move diagnoses, hypotheses, optimization, holdouts, stability, staging,
promotion review, champion storage, lessons, and retention checks into
`src/spy_der/learning/`.

Paths:

```
/var/lib/spy-der/configs/champion.json
/var/lib/spy-der/configs/challengers/
/var/lib/spy-der/configs/pending_review/
/var/lib/spy-der/configs/promoted/
/var/lib/spy-der/configs/champion_history/
```

Promotion is automatic and evidence-gated: the learner stages a challenger, the
Dojo re-runs recorded tape and blind days with that change installed, and
`champion.json` is written only when the re-run beats the incumbent on every
gate. The human path (`promote_pending(..., human_ack="PROMOTE")`) still works.
See `docs/ops/dojo.md` for the gate table and the kill switches.

### Phase 6 — 0DTE as dashboard consumer

0DTE retains a thin adapter that reads:

```
/var/lib/spy-der/live_state.json
/var/lib/spy-der/reports/dojo/latest.json
```

or queries the SPY-DER HTTP service. It must not know provider, model IDs as
internals, prompts, Dojo training logic, or promotion mechanics beyond the
dashboard contract fields.

## Status

| Item | Status |
|---|---|
| Ownership boundary docs | Done |
| Integration contracts | Done |
| Experience-provider protocols | Done |
| Dojo runner scaffold (protocol-driven) | Done |
| Learning staging scaffold | Done |
| HTTP `/v1/decision` service | Done |
| `spy-der-dojo-*` deploy units | Done |
| CandidateEvaluator wiring (recorded / universe / sequential) | Done (Phase 4) |
| Champion / challenger / baseline DecisionAuthority scoring | Done (Phase 4) |
| Retention-panel + gated staging + lesson persistence | Done (Phase 4) |
| Full behavioral parity with every 0DTE Dojo backtest knob | Partial — 0DTE must supply labeled outcomes / SyntheticUniverseProvider |
| 0DTE repo deletions / thin adapter land | Deferred — requires 0DTE PR (this agent cannot push there) |
