# Evaluating HKUDS/Vibe-Trading for SPY-DER

**Decision: do not fork it. Take the dashboard's information design, add a
read-only MCP server, and build the shadow account natively.**

Date: 2026-07-26 · Upstream evaluated: <https://github.com/HKUDS/Vibe-Trading>
(MIT, ~27.6k stars, daily commits)

## The proposal

Fork Vibe-Trading and reuse roughly 30–50% of its platform — agent
orchestration, FastAPI/MCP plumbing, React dashboard, memory and reports,
data-loader framework, validation utilities, research-session management,
backtest-job management, walk-forward and Monte Carlo, journaling and
attribution, paper-trading workflow — while keeping SPY-DER's predictive stack
proprietary. Vibe-Trading would sit above SPY-DER and call it through
`spyder.predict(...)`.

Its *cautions* were all correct: an LLM must never control capital directly,
their options engine is too generic for 0DTE, a moving main branch is a
dependency risk, free Yahoo data is unfit for intraday options work, and
SPY-DER must not become a prompt inside someone else's agent.

The prescription does not follow from them.

## What the reuse list looks like against this repository

Measured at 33,186 LOC across 29 subpackages.

| Proposed for reuse | Already owned by SPY-DER |
| --- | --- |
| Agent orchestration | `spy_der.agents` — 2,766 LOC/19 files: registry, protocols, packet, prompts, parser, validation, security, transport, usage metering, review, comparison, recorded/mock replay agents |
| Research agents (quant/TA/options/risk) | `spy_der.decisions` (727) + `spy_der.policies` + `agents/authority.py`, `agents/review.py` |
| Walk-forward / Monte Carlo / bootstrap | `training/folds.py`, `dojo/sequential.py`, `forecasting/uncertainty.py`, `forecasting/conformal.py`, `forecasting/ood.py` |
| Backtest-job management | `spy_der.dojo` (2,121/14 files) + `dojo/runner.py` + `spy_der.synthetic` (2,603) |
| Data-loader framework | `market_data/providers/base.py` — `MarketDataProvider` protocol + `RawTick`, plus composite failover, freshness, assembler, calendar, recording/replay (2,022) |
| Journaling / model attribution | `spy_der.journal` (append-only, hash-chained, `snapshot_id`-keyed) + `spy_der.learning` + `evaluation/comparison.py`, `replay/comparison.py`, `agents/comparison.py` |
| Paper-trading workflow | `execution/simulator.py` (seeded partial fills), `execution/accounts.py`, `execution/guard.py` |
| Report generation | `dojo/reports.py`, `evaluation/reports.py`, `runtime/artifacts.py` |
| Memory | `learning/memories.py` — lessons and failure episodes |
| **FastAPI / MCP plumbing** | **Missing.** Runtime deps are five scientific packages; `runtime/dashboard_api.py` is stdlib `http.server` |
| **React dashboard** | **Missing.** No `package.json`, no `.tsx`/`.jsx` anywhere |

Nine of eleven already exist. The reusable surface was the UI and an MCP
interface — closer to 5% than 30–50%, and the least differentiated part of the
platform.

The LLM plumbing is not missing either: `agents/transport.py` is an
OpenAI-compatible HTTP transport with no LangChain and no vendor SDK.

## Three places SPY-DER is ahead of the upstream

1. **Validation.** `training/folds.py` builds session-grouped expanding folds
   with whole-session embargo — trading sessions treated as the non-splittable
   group. That is strictly more correct for 0DTE than generic bar-based
   walk-forward. And the real test is prediction-cone calibration, which is
   `forecasting/conformal.py` + `uncertainty.py` + `ood.py`. Vibe-Trading has no
   answer there.

2. **Options execution realism.** The proposal said to build this separately
   because their engine is generic. Right conclusion, stale premise:
   `spy_der.economics` already has `fill_prior`, `half_spread_cost`,
   natural-vs-mid pricing, per-leg and per-contract fees, and separate exit and
   stop fill boosts.

3. **AI containment.** "The LLM must never control capital" is enforced
   structurally here, not by convention. `execution/guard.py` re-derives every
   limit from the candidate set and account state rather than reading it off the
   decision, so a decision can only shrink exposure, and a candidate id outside
   the eligible set is blocked outright. Replacing that with a tool-calling
   agent loop is a safety regression.

## The architectural objection

The proposal puts a Vibe-Trading fork above SPY-DER. SPY-DER's runtime is
already the top of its own stack: ten console entry points, systemd units, a
read-only state root.

`docs/OWNERSHIP_BOUNDARY.md` records why the previous architecture was
abandoned. The old rule made SPY-DER "permanently dependent on packets published
by the system it was meant to replace," which broke parity validation and made
synthetic sparring hollow. Seventeen migration phases and a
`test_deploy_independence.py` that fails CI on any legacy path reference exist to
escape that. Forking an upstream with daily commits and mounting it above
SPY-DER re-creates the same shape against a new dependency.

The cost math does not work either. Per `docs/CAPABILITY_MATRIX.md`, the largest
remaining gap to independence is four vendor adapters — Tradier landed in
`0bf2fd3` — plus parity sign-off on features and forecasting. A fork advances
neither, while adding LangChain, FastAPI, React 19, Vite, 18 data loaders, 10
broker connectors, 16 IM adapters and 452 factors to a repository that passes
`mypy --strict` on five dependencies.

One operational note: the upstream README carries a warning that an X account,
a Virtuals project and tokens are impersonating the project. Not disqualifying,
but it argues against "fork and pull from main regularly."

## What was taken instead

### 1. The dashboard, written natively

`src/spy_der/runtime/ui/` — decision chain, current decision, shadow account,
system health, Dojo and parity, open positions. Served two ways from one
implementation: standalone at `/ui` on `spy-der-dashboard-api`, and as a tab in
the 0DTE Vercel dashboard. No build step, no dependencies, style-scoped under
`.spyder-tab`, and read-only.

See [`docs/ops/DASHBOARD_TAB.md`](ops/DASHBOARD_TAB.md).

### 2. A read-only MCP server

`src/spy_der/runtime/mcp_server.py` — stdio JSON-RPC over the stdlib, exposing
system status, live state, Dojo and validation reports, and the shadow account.

Every tool is a thin wrapper over `dashboard_api.handle_get`, the same pure
handler the HTTP API uses, whose only capability is opening files under the
state root. `tests/unit/test_mcp_server.py` fails structurally if a tool is
added that could act. The decision path stays reachable only through the
deterministic guard; a chat transport must not become a second way in.

### 3. The shadow account, built natively

`src/spy_der/evaluation/attribution.py` — the one genuinely good idea in the
upstream, landed on the existing journal and outcome substrate rather than
imported.

It runs the approved decision and what actually happened side by side and
decomposes the difference into a waterfall that reconciles exactly:

```
model_pnl + participation + selection + sizing + entry + exit = actual_pnl
```

Each stage changes one attribute of the trade and is priced at the stage before
it, so components sum to the gap by construction — there is no residual bucket.
`assert_reconciles` is public because a decomposition that does not add up is
worse than none.

The `verdict` field names which side is costing money: `healthy`,
`execution_drag`, `model_weak`, or `model_weak_and_execution_drag`. That is the
question a single P&L number cannot answer, and answering it is the whole point
of keeping two books.

Behavioural flags — missed signal, unapproved trade, over/undersized, late
entry, premature exit, held past plan, overtrading, revenge trade — describe
*what* diverged. They carry no severity and no P&L, because the waterfall
already says what each divergence cost.

## What was deliberately not done

- No fork, no vendored upstream code, no new runtime dependency. The dependency
  list is unchanged at five packages.
- No LLM-authored strategy execution. Nothing added here can place, size,
  approve or promote.
- No new contract fields. The decision-chain panel renders what the packet
  actually carries and marks the rest "not published" rather than inferring it.
  Publishing `eligible_count`, `considered_count` and a guard verdict on
  `DashboardPacket` would complete that panel; it is a contracts change and was
  left out on purpose.

## Revisit if

- SPY-DER needs multi-asset or multi-broker coverage — the broker and loader
  breadth is the upstream's real advantage, and none of it is relevant to SPY
  0DTE today.
- A specific, isolated component proves better than its SPY-DER counterpart on
  measured evidence. Vendor that component, with a pinned version. Do not invert
  the dependency to get it.
