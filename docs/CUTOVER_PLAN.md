# Cutover Plan — retiring 0DTE

Companion to `docs/TARGET_ARCHITECTURE.md` (the target) and
`docs/CAPABILITY_MATRIX.md` (what has actually moved).

## Sequence

| # | Step | Gate to advance |
|---|---|---|
| 1 | Stop new development in 0DTE | Repository accepts only migration-support commits |
| 2 | Merge the temporary bridge | 0DTE PR #150 — **done** |
| 3 | Port the full deterministic runtime | Every ledger rule at `status: done`; vendor adapters landed |
| 4 | Run parallel validation | All parity gates below pass at declared tolerance |
| 5 | Make SPY-DER the authoritative shadow runtime | 20 consecutive sessions of live-shadow parity |
| 6 | Move dashboard reads to SPY-DER | `dashboard_compatibility` green against the unchanged Vercel frontend |
| 7 | Make SPY-DER the authoritative production runtime | Human sign-off; rollback rehearsed |
| 8 | Disable every 0DTE service | No `zerodte-*` unit enabled or running |
| 9 | Archive the 0DTE repository read-only | Archive flag set; workflows disabled |
| 10 | Remove temporary compatibility adapters from SPY-DER | `spy_der.integrations.zerodte` deleted; no import of it remains |

Steps 3 and 4 are the bulk of the work. Steps 8–10 are irreversible in practice,
so 7 requires an explicit human acknowledgement — SPY-DER does not self-promote
(`spy_der.learning.promotion` already enforces this for models).

## Parity gates and tolerances

Before retirement, run 0DTE and SPY-DER in parallel from the **same** recorded
and live inputs. Identical inputs are asserted first
(`spy_der.runtime.parity.assert_identical_inputs`) — a parity result computed
over different inputs is worthless.

Tolerances are explicit. Anything not listed here has no tolerance and must
match exactly.

| Quantity | Tolerance | Rationale |
|---|---|---|
| Candidate IDs | **exact** | Content-addressed over geometry; any difference is a real divergence |
| Candidate geometry hash | **exact** | Same |
| Maximum loss | **exact** | Decimal throughout on both sides; there is no rounding to permit |
| Capital required | ± 1 cent | Rounding-order differences only |
| Hard vetoes | **exact**, set equality | A veto that fires on one side and not the other is a safety defect, never a tolerance |
| Position sizing (contracts) | **exact** | Integer |
| Size scalar | ± 1e-9 | Float representation |
| Settlement | **exact** | Realized outcome, not an estimate |
| Forecast probabilities | ± 1e-6 (epsilon) | Accumulated float order-of-operations |
| Forecast cone bounds | ± 1e-6 relative | Same |
| Feature values | ± 1e-9 relative | Same |
| Regime labels | **exact** | Discrete |
| Raw market snapshots | **exact** after canonicalization | Both sides canonicalize the same tick |
| Journal output | **exact** on payload content hash | Event ordering may differ; content may not |

Two notes on where tolerances are deliberately absent:

- **Hard vetoes are exact, in both directions.** A SPY-DER veto that 0DTE does
  not raise is as much a failure as the reverse: it means the two systems
  disagree about safety, and the parity run cannot tell you which is right.
- **Maximum loss has no epsilon.** Both sides carry `Decimal`. An epsilon here
  would only hide a units or multiplier bug.

Float comparisons use relative tolerance where the quantity is scale-free and
absolute tolerance where it is bounded (probabilities). Note that
`tests/parity/test_structural_parity.py` currently compares a golden JSON with
exact float equality and drifts in the last digit
(`0.23787117295408164` vs `...158`) across platforms — that gate needs the same
explicit-tolerance treatment.

## Required suites

SPY-DER must pass all of:

| Suite | What it proves | Status |
|---|---|---|
| Recorded-session replay parity | Same recorded tape in, same decisions out | Harness present (`spy_der.replay`), gates pending |
| Synthetic-world parity | Both sides agree on generated worlds | `spy_der.synthetic` native; cross-system gate pending |
| Live shadow parity | Agreement on live ticks over 20 sessions | Pending |
| Restart / recovery | State reconstructs from the journal after a hard kill | `spy_der.positions.restart`, `spy_der.journal.reconstruction` |
| Stale-feed | Degraded and missing components fail closed | `spy_der.market_data.freshness`; guard blocks on `quote_stale` |
| Provider-outage | Failover across providers, then clean abstention | `spy_der.market_data.composite`; needs live adapters to be meaningful |
| Dashboard compatibility | The unchanged Vercel frontend renders SPY-DER state | Pending |

## Target deployment

```
spy-der-market.service
spy-der-engine.service
spy-der-agent.service
spy-der-settlement.service
spy-der-dashboard-api.service
spy-der-dojo-daily.timer
spy-der-dojo-recent.timer
spy-der-dojo-weekly.timer
spy-der-validation-daily.timer
spy-der-validation-weekly.timer
```

State:

```
/var/lib/spy-der/
├── market/
├── chains/
├── bars/
├── forecasts/
├── candidates/
├── decisions/
├── positions/
├── settlements/
├── journal/
├── reports/
├── memories/
├── lessons/
├── configs/
└── usage/
```

Configuration:

```
/etc/spy-der/spy-der.env
/etc/spy-der/config.yaml
```

After final cutover there must be **no runtime reference** to `/opt/zerodte`,
`/var/lib/zerodte` or `/etc/zerodte`. This is enforced, not merely intended:
`tests/unit/test_deploy_independence.py` scans `deploy/` and `src/` and fails on
any such path, and on any `zerodte-*` unit name.

## Rollback

Rollback stays available through step 7 and is rehearsed, not assumed
(`spy_der.runtime.parity.rehearse_rollback`,
`spy_der.deployment.rollback.rollback_deployment`). Because 0DTE state lives
under `/var/lib/zerodte` and SPY-DER's under `/var/lib/spy-der`, the two runtimes
never share mutable state and reverting the authoritative runtime is a
unit-enable change, not a data migration.

After step 9 the 0DTE repository is read-only, so rollback past that point means
restoring from the archive — which is why steps 8 and 9 sit behind 20 sessions of
green live-shadow parity.
