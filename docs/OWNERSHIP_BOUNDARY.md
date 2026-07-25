# Ownership Boundary — SUPERSEDED

> **This document is superseded by [`TARGET_ARCHITECTURE.md`](TARGET_ARCHITECTURE.md).**
>
> It described an interim architecture in which 0DTE remained the production
> market engine and SPY-DER owned only decision intelligence. That is no longer
> the target. **SPY-DER becomes the complete production system; 0DTE is absorbed
> and retired.**
>
> It is kept because the interim split is still the *current* runtime state
> during migration, and because the contract table below is the surface the
> temporary bridge speaks. Do not use it to justify leaving a capability in
> 0DTE.

## Why it changed

The old rule was:

> If code collects market information, computes deterministic market features,
> generates candidates, records outcomes, or renders the existing dashboard, it
> belongs in 0DTE.

That rule made SPY-DER permanently dependent on packets published by the system
it was meant to replace. Two consequences forced the change:

1. **The AI could not be validated against its own inputs.** SPY-DER could not
   reproduce a market snapshot, so parity was only ever assertable at the packet
   boundary — not at the feature, forecast or candidate level where the
   interesting divergences live.
2. **Synthetic sparring was hollow.** 0DTE's synthetic provider emitted
   placeholder candidates, so the Dojo's universe phase scored decisions over
   geometry that did not exist. That is now fixed by SPY-DER owning
   `spy_der.synthetic` outright.

## On 0DTE PR #150

PR #150 is a useful interim decoupling step and has been merged. It removed
duplicate AI ownership from 0DTE, established the contracts, let SPY-DER operate
while migration continues, and reduced dangerous in-process coupling.

It is **not** the final architecture:

> 0DTE remains a temporary upstream market provider during full-stack migration.
> The final target is an independently operating SPY-DER system with no runtime
> dependency on 0DTE.

## Interim rule (still in force during migration)

The Dojo, AI agents, prompts, review logic, memories, lessons, promotion
workflows, usage metering and model routing belong in **SPY-DER**. That part of
the old boundary was correct and is unchanged — it is now simply a subset of a
larger claim.

What changed is the other half: market ingestion, chain storage, features,
forecasting, candidate generation, deterministic risk, journal, settlement and
dashboard data services are **also** SPY-DER's, on the schedule in
`docs/CUTOVER_PLAN.md`.

## Contract schemas (the temporary bridge)

These remain valid until cutover step 10, after which `MarketPacket` is an
internal boundary or external API schema rather than a cross-repository
dependency.

| Direction | Schema | Module |
|---|---|---|
| 0DTE → SPY-DER | `zerodte.spyder.market.v1` | `spy_der.contracts.integration.MarketPacket` |
| 0DTE → SPY-DER | `zerodte.spyder.outcome.v1` | `spy_der.contracts.integration.OutcomePacket` |
| SPY-DER → 0DTE | `spyder.dashboard.v1` | `spy_der.contracts.integration.DashboardPacket` |
| 0DTE ↔ SPY-DER | `spyder.decision.request.v1` / `spyder.decision.response.v1` | HTTP `/v1/decision` |

Synthetic universes are **no longer** part of this surface: the Dojo calls
`spy_der.synthetic.SyntheticUniverseProvider` natively.

## Interim runtime layout

```
/opt/zerodte          0DTE source + venv          (removed at cutover)
/opt/spy-der          SPY-DER source + venv
/var/lib/zerodte      0DTE state                  (removed at cutover)
/var/lib/spy-der      SPY-DER state — see docs/TARGET_ARCHITECTURE.md
```

The target layout, services and timers are in `docs/CUTOVER_PLAN.md`. After final
cutover there is no runtime reference to `/opt/zerodte`, `/var/lib/zerodte` or
`/etc/zerodte`.
