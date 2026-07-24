# 0DTE thin SPY-DER dashboard adapter

This agent cannot push to `DGator86/0DTE`. Land the following on 0DTE `main`.

## Target layout inside 0DTE

```
0DTE/integrations/spy_der/
    contracts.py          # schema constants + parsers for spyder.dashboard.v1
    dashboard_reader.py   # read /var/lib/spy-der/live_state.json (+ dojo latest)
    decision_client.py    # optional: POST http://127.0.0.1:8787/v1/decision
```

## What to remove from 0DTE (after cutover)

- `dojo.py`, `sequential_dojo.py`
- `docs/dojo.md`, `docs/sequential_dojo.md`
- `tests/test_dojo.py`, `tests/test_sequential_dojo.py`
- `deploy/zerodte-dojo-*`
- In-process imports of `spy_der.integrations.zerodte.decide_shadow_tick`
  (replace with HTTP client or filesystem outbox)

Keep market data, forecasting, candidates, journal, shadow execution, and the
Vercel dashboard.

## Reader contract

Read only:

```
/var/lib/spy-der/live_state.json
/var/lib/spy-der/reports/dojo/latest.json
```

Expected `live_state.json` schema: `spyder.dashboard.v1` (see
`spy_der.contracts.integration.DashboardPacket`).

0DTE must not import SPY-DER agents, prompts, Dojo, or learning modules.

## Decision client (preferred)

```python
# pseudocode for 0DTE shadow loop
POST http://127.0.0.1:8787/v1/decision
{
  "schema_version": "spyder.decision.request.v1",
  "market": { "schema_version": "zerodte.spyder.market.v1", ... }
}
```

Response: `spyder.decision.response.v1` wrapping `spyder.dashboard.v1`.
