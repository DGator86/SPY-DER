# SPY-DER

SPY-DER is the complete, independent SPY 0DTE trading-research and decision
system: market-data ingestion, options-chain storage, features, forecasting,
candidate generation, deterministic risk, decision authority, execution,
settlement, journal and replay, synthetic universes, Dojo and learning,
promotion governance, and dashboard data services.

`DGator86/0DTE` is the legacy implementation being absorbed and retired. It is a
**temporary** upstream market provider during migration, not a permanent
dependency.

- **Target:** [`docs/TARGET_ARCHITECTURE.md`](docs/TARGET_ARCHITECTURE.md)
- **What has actually moved:** [`docs/CAPABILITY_MATRIX.md`](docs/CAPABILITY_MATRIX.md)
- **Retirement sequence and parity tolerances:** [`docs/CUTOVER_PLAN.md`](docs/CUTOVER_PLAN.md)

## Package highlights

- `spy_der.market_data` — providers, ordered failover, canonical snapshots, replay
- `spy_der.features` — GEX, multi-timeframe, RND, volatility, structural state
- `spy_der.forecasting` — regime labels, ensembles, cones, uncertainty
- `spy_der.candidates` — geometry enumeration, payoff proofs, dominance filtering
- `spy_der.risk` — deterministic eligibility, vetoes, sizing, portfolio envelope
- `spy_der.decisions` — AI decision authority (Grok, deterministic fallback)
- `spy_der.execution.guard` — deterministic execution guard; the AI is advisory
- `spy_der.journal` — append-only, hash-chained, keyed by `snapshot_id`
- `spy_der.synthetic` — SPY-DER-owned synthetic universes (Markov worlds,
  coupled chain repricing, coverage matrix, calibration, archetype evolution)
- `spy_der.dojo` — protocol-driven Dojo over recorded and synthetic experience
- `spy_der.learning` — diagnose / optimize / stage a challenger, then promote it
  automatically when a re-run under that change beats the incumbent on every gate
- `spy_der.evaluation.attribution` — shadow account: model quality vs execution
  quality, decomposed into a waterfall that reconciles exactly
- `spy_der.runtime.mcp_server` — read-only MCP surface over published state
- `spy_der.runtime.ui` — the dashboard tab, served standalone and embedded
- `spy_der.contracts.integration` — versioned packets (temporary bridge surface)

## Decision hierarchy

The AI may choose among *valid* candidates. Deterministic code has the last word.

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

The guard re-derives every limit from the candidate set and account state rather
than reading it off the decision, so a decision cannot assert its way past a
limit. It can only shrink exposure. A candidate id that is not in the eligible
set is blocked outright — candidate invention is structurally impossible.

## Local checks

```bash
python -m pip install -e .[dev]
python -m ruff check .
python -m mypy src
python -m pytest
```

## Dashboard and MCP

```bash
spy-der-dashboard-api --state-root /var/lib/spy-der   # then open /ui
spy-der-mcp --state-root /var/lib/spy-der             # stdio MCP, read-only
```

Both are read-only over published state and share one read path
(`dashboard_api.handle_get`). Neither can place, size, approve or promote
anything. See [`docs/ops/DASHBOARD_TAB.md`](docs/ops/DASHBOARD_TAB.md).

**Public Vercel page (SPY-DER-owned):** [spy-der.vercel.app](https://spy-der.vercel.app)
— static shell + Node `/api` gateway on the `spy-der` Vercel project. It needs no
configuration to show live data: without a tunnel to `spy-der-dashboard-api` it
reads published state through the 0DTE host, and upgrades itself to the full
`/v1` surface once `SPY_DER_DASHBOARD_URL` is set. See
[`docs/ops/VERCEL_DASHBOARD.md`](docs/ops/VERCEL_DASHBOARD.md). Optional
0DTE embed: [`integrations/zerodte/spy_der_tab/`](integrations/zerodte/spy_der_tab).

## Deployment

Units and config templates are in [`deploy/`](deploy). Install
`spy-der.env.example` to `/etc/spy-der/spy-der.env` and `config.yaml.example` to
`/etc/spy-der/config.yaml`; state lives under `/var/lib/spy-der`.

`tests/unit/test_deploy_independence.py` fails on any reference to a legacy 0DTE
path or unit name, so deployment independence is enforced rather than intended.

## Intentionally not included

- No live brokerage integration
- Live routing is opt-in and fails closed (`GuardLimits.allow_live` is `False`)
- No autonomous promotion — a human acknowledgement is required
