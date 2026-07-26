# ARCHITECTURE

## Ownership (authoritative)

See `docs/OWNERSHIP_BOUNDARY.md`.

- **0DTE** owns market-data ingestion, options chains, technical features,
  forecasting, candidate generation, deterministic risk/strategy calculations,
  shadow execution records, the Vercel dashboard, and a thin SPY-DER adapter.
- **SPY-DER** owns decision intelligence: agents, prompts, review, Dojo,
  Sequential Dojo, adaptive learning, memories/lessons, promotion governance,
  model routing, usage metering, and AI VPS services.

Integration is only through versioned contracts in
`spy_der.contracts.integration`.

## Research scaffold (Phases 0–17)

System B also contains a typed research/parity scaffold:

1. Legacy structural evidence + permissions + hard vetoes
2. V2 underlying forecasts
3. Deterministic candidate factory (bounded-risk options only)
4. V3 candidate economics and ranking
5. Synthesis of approved candidates or abstention
6. Deterministic risk firewall with final authority
7. Execution + positions as explicit state machines
8. Journal + replay + comparison harness

That scaffold remains for parity and offline research. Production ownership of
market/forecast/candidate pipelines stays with 0DTE.

## Live boundary packages

- `spy_der.contracts.integration` — MarketPacket / OutcomePacket / DashboardPacket
- `spy_der.dojo` — protocol-driven Dojo (no 0DTE internal imports)
- `spy_der.learning` — adaptive learning + evidence-gated automatic promotion
- `spy_der.integrations.zerodte` — HTTP client, filesystem experience feed,
  dashboard publisher, legacy in-process provider (migration only)
- `spy_der.runtime.decision_service` — local `POST /v1/decision`
