# SPY-DER Staged Fix Plan

## Diagnosis

SPY-DER has advanced forecasting, candidate, AI, risk, synthetic-world, dashboard, and deployment code, but its execution and accounting foundation is not yet trustworthy enough to measure strategy quality. The frozen model handoff is disciplined, but it was selected and re-tested on synthetic worlds. Real point-in-time replay remains mandatory.

The repair order is therefore:

> execution and accounting truth → causal real-data replay → narrow deterministic strategy → AI challenger → controlled paper evidence → cutover.

## Phase 0 — Governance and frozen baseline

- Pin exact SPY-DER and 0DTE source commits.
- Declare SPY-DER the sole target platform.
- Import the frozen model configuration as an immutable, versioned baseline.
- Protect it with checksum/version tests.
- Reconcile README, architecture, capability matrix, cutover plan, and handoff state.

**Gate:** one authoritative source hierarchy; no contradictory ownership or authority statements.

## Phase 1 — Economic accounting truth

Canonical candidate economics must include:

- `entry_type = NET_DEBIT | NET_CREDIT`;
- contract multiplier;
- signed opening cash flow;
- maximum profit and maximum loss;
- capital required;
- immutable legs and payoff hash.

P&L must be calculated as:

```text
opening signed cash flows
+ closing signed cash flows
+ settlement cash flows
- entry fees
- exit fees
- exercise/assignment fees
```

Do not infer economic direction from unsigned mark movement.

Required tests: debit and credit winners/losers, partial fills, partial reductions, fees, expiration, maximum loss, maximum profit, multiplier, and account-equity reconciliation.

**Gate:** cash, equity, realized P&L, unrealized P&L, and event cash flows reconcile exactly at declared precision.

## Phase 2 — Order provenance and guard enforcement

- Remove default `buy_to_open` behavior.
- Derive opening and closing actions from candidate economics.
- Introduce `ValidatedOrderIntent`.
- Only the deterministic execution guard may construct it.
- Prevent direct executor calls from AI, policy, or runtime code.
- Revalidate candidate identity, geometry, maximum loss, buying power, quote freshness, session state, position limits, concentration, lockouts, and duplicates immediately before submission.

**Gate:** repository tests prove no unguarded order can reach any executor.

## Phase 3 — Realistic entry execution

Submission creates a working combination order, not an immediate fill.

Record:

- initial combination bid/mid/ask;
- limit price;
- order age;
- subsequent quote states;
- fill price and latency;
- fill concession;
- partial quantity;
- cancel, reject, or expiry reason;
- underlying movement during latency.

Provide conservative, calibrated, and optimistic fill modes. Optimistic is sensitivity only. Champion evaluation uses calibrated and conservative results.

**Gate:** no order fills unless subsequent market evidence supports that fill.

## Phase 4 — Realistic exits

All closes and reductions become closing orders through the same execution machinery.

Required states include:

```text
OPEN
EXIT_REQUESTED
EXIT_WORKING
PARTIALLY_EXITED
CLOSED
EXPIRED
SETTLED
RECONCILIATION_ERROR
```

Deterministic mandatory exits override AI HOLD for emergency, expiration, EOD, hard-loss, stale-data liquidation, and reconciliation failure.

**Gate:** no position is closed merely because a caller supplied an arbitrary mark.

## Phase 5 — Canonical point-in-time data plane

Every real observation retains:

- `event_time`;
- `received_at`;
- `available_at`;
- source;
- revision;
- quality state.

Implement canonical contracts for constituent bars, SPY bars and quotes, option contracts and quotes, open interest, index membership and weights, market calendar, and scheduled events. Complete native Massive, Tradier, Tastytrade, and approved Yahoo fallback adapters.

Late revisions are append-only and never rewrite what an earlier forecast knew.

**Gate:** recorded replay reconstructs the exact legal information set at each forecast origin.

## Phase 6 — Frozen feature and forecast runtime

Preserve the frozen model roles:

- 5m: active, low edge, timing/context;
- 15m: primary active;
- 30m: primary active with exact reduced feature set;
- 60m: advisory only;
- endpoint quantile intervals: active;
- state alerts: shadow only;
- path fans: disabled.

Implement the 11 frozen feature blocks once. Enforce the 30m exclusions: SPY momentum, original tensor, and GEX level. Freeze all training medians/MADs, masks, Ridge alphas, quantile weights, and conformal offsets.

Treat GEX as susceptibility, damping/amplification, and location—not a standalone directional oracle.

**Gate:** synthetic parity fixtures reproduce; runtime outputs are provenance-aware and deterministic.

## Phase 7 — Real-market frozen replay

Run the unchanged frozen models against:

1. zero return;
2. price persistence;
3. SPY momentum;
4. SPY VWAP/Bollinger;
5. frozen full model;
6. frozen reduced 30m model;
7. advisory 60m model.

Measure MAE, median error, direction, calibration, interval coverage and width, latency, unavailable rate, and performance by time of day, volatility, GEX, events, and data quality.

Run future-mutation and time-of-day-preserving constituent-placebo tests.

Evaluate only the pre-registered state alerts before any new state search. Record event counts, MFE, MAE, post-cost expectancy, day-block bootstrap, and FDR.

**Gate:** 15m and/or 30m retain positive day-block bootstrap lower bounds and aligned constituent fields beat placebos. Failure returns the model to research.

## Phase 8 — Narrow deterministic options champion

Initial scope:

- SPY only;
- 0DTE only;
- one position;
- one contract;
- vertical spreads only;
- 15m and 30m primary;
- 5m timing/context;
- 60m advisory;
- deterministic champion;
- AI shadow challenger;
- live orders disabled.

Every candidate uses shared expected and conservative fills, fees, slippage, EV, utility, CVaR, maximum loss, liquidity, touch probability, uncertainty, and data-quality penalties.

**Gate:** no positive conservative utility means `NO_EDGE`.

## Phase 9 — One authoritative runtime

One canonical tick owns:

1. snapshot assembly;
2. freshness validation;
3. frozen features;
4. forecasts;
5. candidates;
6. executable economics;
7. deterministic and AI decisions;
8. risk;
9. entry and exit order updates;
10. position marks;
11. journaling;
12. dashboard publication.

One `snapshot_id` must follow the complete lifecycle. Hard-kill recovery reconstructs working orders, positions, cash, equity, P&L, lockouts, and deployment. Entries remain blocked until reconciliation passes.

**Gate:** reconstruction and live state match exactly after restart.

## Phase 10 — Journal, counterfactuals, and attribution

Persist every:

- trade and no-trade;
- reject, cancel, expiry, and unfilled attempt;
- entry and exit fill;
- position mark;
- settlement;
- policy and AI decision;
- deployment and rollback event.

Store counterfactuals for no-trade, expiration hold, best eligible candidate, midpoint, conservative fill, calibrated fill, deterministic champion, and AI challenger.

Decompose P&L into:

```text
forecast quality
+ candidate-selection value
+ entry-execution value
+ exit-management value
- fees
- slippage
= net result
```

**Gate:** every report rebuilds entirely from journal events.

## Phase 11 — Operator dashboard

The first screen answers:

- What is the system doing?
- Is data healthy?
- Is an order working?
- Is a position open or closing?
- Is risk blocking anything?
- What requires operator attention?

Show endpoint forecasts and intervals only. Do not draw a smooth forecast fan. Missing state is displayed as absent and never invented as idle or pending.

## Phase 12 — Validation, paper pilot, and cutover

Engineering gates:

- causal timestamp compliance;
- deterministic replay;
- exact candidate IDs, geometry, and maximum loss;
- no executor bypass;
- realistic entry and exit orders;
- exact ledger reconciliation;
- restart recovery;
- stale-feed and provider-outage tests;
- dashboard compatibility;
- rollback rehearsal.

Migration requirement: at least 20 consecutive green identical-input live-shadow sessions.

Recommended strategy evidence: at least 60 settled sessions across several regimes with positive conservative-fill post-cost expectancy, acceptable drawdown, and no dependence on a handful of outliers.

Controlled paper scope remains one-contract SPY 0DTE verticals in an isolated account. No autonomous promotion. No live broker credentials.

After all gates: make SPY-DER authoritative, disable 0DTE services, archive 0DTE read-only, remove compatibility adapters, and prove no old runtime paths remain.

## Ordered work queue

1. Frozen baseline and provenance
2. Pin 0DTE baseline
3. Documentation authority reconciliation
4. Explicit debit/credit candidate economics
5. Signed cash-flow P&L
6. Contract multiplier enforcement
7. Remove order-side defaults
8. `ValidatedOrderIntent`
9. Wire risk and execution guard
10. Working combination orders
11. Calibrated fill model
12. Partial/cancel/expiry handling
13. Exit order state machine
14. Exact ledger reconciliation
15. Three-timestamp observations
16. Native provider adapters
17. Immutable recorded-session store
18. Frozen feature parity
19. Frozen forecast serving
20. Real-data replay
21. Pre-registered alert evaluation
22. Narrow vertical-spread champion
23. One authoritative runtime
24. Restart reconstruction
25. Append-only attribution
26. AI shadow challenger
27. Operator-first dashboard
28. Identical-input 0DTE/SPY-DER parity
29. Controlled paper pilot
30. 0DTE retirement

Complete these in order unless a documented dependency requires otherwise. One behavioral concern per PR.