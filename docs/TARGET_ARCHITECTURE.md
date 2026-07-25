# Target Architecture — SPY-DER as the complete production system

**Status: authoritative.** This document supersedes `docs/OWNERSHIP_BOUNDARY.md`,
which described an interim split where 0DTE remained the production market engine
and SPY-DER was the AI brain. That split is no longer the target.

The guiding rule is no longer *"0DTE is the market engine and SPY-DER is the AI
brain."* It is:

> **SPY-DER is the complete independent trading-research and decision system.
> 0DTE is the legacy implementation being absorbed and retired.**

## Target ownership

```
SPY-DER
├── market-data ingestion
├── options-chain storage
├── technical features
├── forecasting
├── candidate generation
├── deterministic risk
├── decision authority
├── shadow/live execution
├── settlement
├── journal and replay
├── synthetic universes
├── Dojo and learning
├── promotion governance
├── API
└── dashboard data services

0DTE
└── deprecated after parity and cutover
```

| Capability | Final owner |
|---|---|
| Market ingestion | SPY-DER |
| Options chains | SPY-DER |
| Features | SPY-DER |
| Forecasts | SPY-DER |
| Candidates | SPY-DER |
| Risk and vetoes | SPY-DER |
| Decisions | SPY-DER |
| Execution | SPY-DER |
| Journal and settlement | SPY-DER |
| Replay and synthetic universes | SPY-DER |
| Dojo and learning | SPY-DER |
| Dashboard API | SPY-DER |
| 0DTE | Archived |

## On 0DTE PR #150

PR #150 removed duplicate AI ownership from 0DTE, established the versioned
contracts, and cut the dangerous in-process coupling. It is worth merging and it
has been merged. **It is not the endpoint.**

> 0DTE remains a temporary upstream market provider during full-stack migration.
> The final target is an independently operating SPY-DER system with no runtime
> dependency on 0DTE.

Concretely, that means the artifacts PR #150 introduced are scheduled for
deletion, not maintenance:

- `zerodte/**` and `integrations/**` in the 0DTE repository are marked
  `delete` in `migrations/inventory/zerodte_disposition.json`.
- `spy_der.integrations.zerodte` in this repository is a temporary
  compatibility surface that re-exports SPY-DER-owned modules. It holds no
  logic of its own and is removed at cutover step 10.
- `MarketPacket` stops being a cross-repository dependency and becomes an
  internal boundary or external API schema.

## Runtime shape

```
external providers  (Massive/Polygon, Tradier, Tastytrade, Yahoo)
        |
spy_der.market_data          <- SPY-DER owns provider integrations
        |
canonical MarketSnapshot
        |
spy_der.features             <- greeks, GEX, OI, vol structure, MTF
        |
spy_der.forecasting          <- regime classification, forecast ensemble, cones
        |
spy_der.candidates           <- spread construction, geometry, payoff proofs
        |
spy_der.risk                 <- deterministic eligibility, vetoes, sizing
        |
spy_der.decisions            <- AI decision authority
        |
spy_der.execution.guard      <- deterministic execution guard
        |
spy_der.execution            <- shadow or live executor
        |
spy_der.journal              <- append-only, hash-chained
        |
spy_der.evaluation           <- settlement, outcomes, labels
        |
spy_der.dojo / spy_der.learning / promotion governance
```

SPY-DER does not need a `MarketPacket` published by 0DTE anywhere in this chain.

## Decision and execution authority

The AI may choose among *valid* candidates. Deterministic code retains final
enforcement.

```
candidate engine
      |
deterministic eligibility      spy_der.risk.firewall
      |
SPY-DER decision authority     spy_der.decisions
      |
deterministic execution guard  spy_der.execution.guard
      |
shadow or live executor        spy_der.execution
```

`spy_der.execution.guard` re-derives every limit from the candidate set and
account state rather than reading it off the decision, so a decision cannot
assert its way past a limit. It enforces:

- hard vetoes (per candidate and per snapshot)
- maximum loss and capital, recomputed from the candidate
- position limits and per-family concentration
- size caps — the guard can only *shrink* size, never widen it
- session restrictions (naive or missing timestamps fail closed)
- liquidity: fill-probability floor and quote freshness
- live-trading permission, which is opt-in and fails closed

The check that matters most: a candidate id the AI names but that is not in the
eligible set is blocked with `candidate_not_eligible`. Candidate invention is
structurally impossible, not merely discouraged.

## Synthetic universes

Synthetic-universe production is SPY-DER's own:

```
spy_der.synthetic
        |
SyntheticUniverseProvider
        |
      Dojo
```

`spy_der.synthetic` absorbed 0DTE's `matrix_universe`, `synthetic_world` and
`regime_calibration`. Generative constants are preserved verbatim, and
`simulator_config_hash()` fingerprints them so a stored Dojo report can never
silently diverge from the code that produced it.

Two things improved in the move, on purpose:

1. **Real candidates.** 0DTE's `SyntheticUniverseProvider` emitted a single
   `"unknown"`-family placeholder per snapshot, so universe sparring could not
   score candidate geometry at all. The native provider reprices a real chain
   each tick and runs SPY-DER's own candidate factory over it.
2. **One canonical path.** `MarkovWorld` implements `MarketDataProvider`, so
   synthetic ticks traverse the same `CompositeFeed -> CanonicalSnapshotAssembler`
   ingestion path as live provider data. A bug in assembly now shows up in
   synthetic sparring instead of hiding until live.

The Dojo's universe phase no longer degrades to `insufficient_data` without
0DTE, and reports a real `(archetype x regime)` coverage matrix — 8 archetypes
by 5 regimes, 40 cells — instead of a count of catalog coordinates.

## Lifecycle identity

One identifier connects the whole lifecycle:

```
snapshot_id
  ├── forecast
  ├── candidates
  ├── decision
  ├── execution
  └── outcome
```

`spy_der.journal` events carry `snapshot_id` alongside the hash chain, so a
settled outcome can be walked back to the market state that produced it.

## Deployment

Services and state are SPY-DER-owned. After cutover there is no runtime
reference to `/opt/zerodte`, `/var/lib/zerodte` or `/etc/zerodte` — see
`docs/CUTOVER_PLAN.md` and the units in `deploy/`, and note that
`tests/unit/test_deploy_independence.py` enforces it.

## What remains

This document states the target. `docs/CAPABILITY_MATRIX.md` records, per 0DTE
module, how far each capability has actually moved — including the parts that
have not. Treat the `status` fields in
`migrations/inventory/zerodte_disposition.json` as the source of truth for
progress, since a test keeps them honest about coverage.
