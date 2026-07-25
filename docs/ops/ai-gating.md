# When the AI runs

The paid model (Grok) runs **during market hours**, or when a caller explicitly
declares its work is not a live tick. Everything else gets the deterministic
agent — no HTTP, no spend.

Outside the session there is no live tape to decide on, so a model call is spend
with nothing behind it.

## The rule

| Situation | Agent |
|---|---|
| Regular session open | Grok |
| Pre-open, after close, weekend, holiday | Deterministic |
| Dojo run, any hour | Grok |
| `SPY_DER_AI=0` / `XAI_ENABLED=0` | Deterministic — outranks everything |
| No `XAI_API_KEY` | Deterministic |
| `SPY_DER_AI_MARKET_HOURS_ONLY=0` | Grok, any hour |

## It downgrades, it never blocks

The gate lives in the decision path, not in the systemd units. That is
deliberate: `spy-der-agent` is an HTTP boundary another process calls. Stopping
the unit out of hours would turn every caller's request into a connection error,
where returning a deterministic decision degrades cleanly and costs nothing.

A decision always comes back. Only its author changes.

## The Dojo is exempt

The Dojo's timers fire at **06:30 ET** — three hours before the open — and
sparring against recorded and synthetic tape is exactly when the model should
run. Gating it would silently downgrade every Dojo run to the deterministic
agent and quietly change what the Dojo measures.

`spy_der.dojo.runner.run_dojo` wraps itself in `ai_context("dojo")`, which lifts
the market-hours gate for the duration. The context is re-entrant and is
released even if a phase raises, so a failed run cannot leave the process
permanently exempt.

Any caller doing offline work over recorded tape can do the same:

```python
from spy_der.decisions.shadow import ai_context

with ai_context("backfill"):
    ...   # model calls here ignore market hours
```

## The killswitch still wins

`SPY_DER_AI=0` outranks the Dojo exemption. An operator turning the AI off means
off, including inside a Dojo run.

## Calendar failures fail *open*

If the exchange calendar cannot be loaded or raises, the gate treats the market
as open and lets the model run.

This is the conservative choice **for this gate specifically**: it is a spend
control, not a safety control. Every real limit — position size, maximum loss,
capital, live-routing — lives in `spy_der.execution.guard` and is unaffected by
any of this. Failing closed here would mean a calendar hiccup silently switches
off the AI mid-session, which is the worse outcome.

## Checking what happened

The agent that ran is on every decision:

```bash
curl -s http://127.0.0.1:8788/v1/state | jq '.trader_model, .provider'
```

A deterministic decision out of hours is the gate working, not a fault.
