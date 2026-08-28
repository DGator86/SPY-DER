# SPY-DER Repair Handoff for Codex

This branch exists so Codex can read the repair program directly from GitHub. No ZIP upload is required.

## Target and authority

- Target repository: `DGator86/SPY-DER`.
- `DGator86/0DTE` is a pinned migration source, parity comparator, temporary market-data bridge, and rollback target only.
- Live brokerage execution is excluded.
- Synthetic-world results establish reproducibility, not a production-tradable edge. Timestamp-accurate real-market replay is mandatory.

## Read first

1. `CODEX_START_HERE.md`
2. `docs/SPY_DER_FIX_PLAN_CODEX.md`
3. `baseline/frozen_models/spy_der_v1/configuration.compact.json`
4. Existing repository documents: `docs/TARGET_ARCHITECTURE.md`, `docs/CAPABILITY_MATRIX.md`, `docs/CUTOVER_PLAN.md`, and `docs/CODEX_HANDOFF_STATE.md`

## Non-negotiable controls

- Work one behavioral concern per pull request.
- Inspect current source before claiming a defect or completion.
- Preserve the frozen 5m, 15m, and 30m model settings exactly.
- Keep 60m advisory-only.
- Keep categorical state alerts shadow-only.
- Keep forecast fans and failed recursive/controlled/analog path models disabled.
- Do not add live broker routing.
- Missing or stale critical inputs fail closed.
- AI may select only an existing bounded-risk candidate and may only reduce deterministic size.
- Deterministic risk and execution controls are final.
- Entries and exits are working orders that may remain unfilled.
- Preserve no-trades, rejected orders, cancellations, expirations, and unfilled attempts.
- Options P&L must use signed cash flows, the contract multiplier, fees, slippage, and settlement.

## Required lifecycle

```text
Point-in-time observations
→ canonical snapshot
→ frozen features
→ horizon forecasts and endpoint intervals
→ bounded candidate universe
→ executable candidate economics
→ deterministic policy and AI challenger
→ deterministic risk firewall
→ deterministic execution guard
→ realistic combination-order state machine
→ position and closing-order state machines
→ exact ledger and settlement
→ append-only journal
→ replay, attribution, dashboard, promotion, rollback
```

## Start here

Begin with **T001: establish and protect the frozen baseline**.

For the first PR:

1. Review the current baseline, migration, and forecasting files.
2. Add the compact frozen configuration under `baseline/frozen_models/spy_der_v1/`.
3. Add provenance explaining that the source handoff is synthetic-world validated only.
4. Add a CI test that fails if the frozen baseline changes without an explicit version bump.
5. Do not modify runtime behavior yet.
6. Run Ruff, MyPy, and Pytest.

End every PR with:

- source inspected;
- behavior changed;
- tests added;
- checks run;
- remaining risks;
- rollback path;
- next numbered task.

Do not jump ahead to AI, dashboards, new models, or live deployment.