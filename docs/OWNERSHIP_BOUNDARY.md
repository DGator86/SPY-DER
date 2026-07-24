# Ownership Boundary — 0DTE ↔ SPY-DER

This document corrects the repository ownership boundary. It supersedes any
reading of `SPY_DER_MASTER_SPEC.md` that treats SPY-DER as the sole owner of
market-data ingestion, forecasting, candidate generation, or the Dojo.

SPY-DER owns **decision intelligence**. 0DTE owns **market infrastructure** and
the existing Vercel dashboard. Integration is only through versioned contracts.

## Immediate rule

If code chooses, interprets, learns, reviews, remembers, routes models, or
promotes AI behavior, it belongs in **SPY-DER**.

If code collects market information, computes deterministic market features,
generates candidates, records outcomes, or renders the existing dashboard, it
belongs in **0DTE**.

The Dojo unequivocally falls into the first category.

## Correct repository boundary

### `DGator86/0DTE` owns

- Market-data ingestion
- Options-chain storage
- Technical feature generation
- Forecasting and candidate generation
- Deterministic risk and strategy calculations
- Shadow execution records
- The current dashboard
- A thin SPY-DER dashboard adapter

### `DGator86/0DTE` must not own

- Dojo / Sequential Dojo
- AI agents, Grok adapters, prompts, review logic
- AI memories, lessons, promotion workflows
- AI usage metering, model routing, training schedules

### `DGator86/SPY-DER` owns

- Grok trader / reviewer and provider-neutral model interfaces
- Decision packets, entry / position decisions, authority policies
- AI prompts, parsing, usage and cost tracking
- Dojo, Sequential Dojo, adaptive learning
- Agent lessons and episodic memory
- Champion/challenger governance and promotion review
- Synthetic-universe AI evaluation
- Model routing / escalation
- AI-specific VPS services and timers

## Narrow integration

```
0DTE
  produces market state, forecasts, candidates and outcomes
                         ↓
              versioned data contract
                         ↓
SPY-DER
  decides, learns, reviews and publishes its state
                         ↓
              versioned result contract
                         ↓
0DTE dashboard adapter
  displays SPY-DER status and decisions
```

- 0DTE must not import SPY-DER’s internal agents or training code.
- SPY-DER must consume 0DTE through contracts, files, a database view, or an API.

## Contract schemas

| Direction | Schema | Module |
|---|---|---|
| 0DTE → SPY-DER | `zerodte.spyder.market.v1` | `spy_der.contracts.integration.MarketPacket` |
| 0DTE → SPY-DER | `zerodte.spyder.outcome.v1` | `spy_der.contracts.integration.OutcomePacket` |
| SPY-DER → 0DTE | `spyder.dashboard.v1` | `spy_der.contracts.integration.DashboardPacket` |
| 0DTE ↔ SPY-DER | `spyder.decision.request.v1` / `spyder.decision.response.v1` | HTTP `/v1/decision` |

## Runtime layout (target)

```
/opt/zerodte          0DTE source + venv
/opt/spy-der          SPY-DER source + venv
/var/lib/zerodte      ticks, chains, forecasts, candidates, settlements, shadow.db
/var/lib/spy-der      live_state.json, decisions/, reports/dojo/, memories/,
                      lessons/, usage/, configs/, challengers/, pending_review/
```

Services:

- `zerodte-market.service`
- `zerodte-dashboard.service`
- `spy-der-agent.service`
- `spy-der-dojo-daily.timer`
- `spy-der-dojo-recent.timer`
- `spy-der-dojo-weekly.timer`

## Relation to existing System B modules

Phases 0–17 built a full research/parity scaffold inside `src/spy_der/`
(including market_data, forecasting, candidates). That scaffold remains for
parity and offline research. **Production ownership** of those capabilities
stays with 0DTE. New Dojo / live-agent work must not deepen in-process coupling
to 0DTE internals; it must go through `spy_der.contracts.integration` and
`spy_der.dojo` experience-provider protocols.
