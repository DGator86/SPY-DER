# Capability Matrix — 0DTE to SPY-DER

Inventory of both repositories with a disposition for **every** 0DTE module.
Nothing is ambiguous: the machine-readable ledger is
`migrations/inventory/zerodte_disposition.json`, pinned against 0DTE
`6393cd110913b9327f57bff176bf3797d03a8c39` (328 tracked paths, the commit that
merged PR #150), and `tests/unit/test_migration_inventory.py` fails if any
tracked path lacks a disposition or any rule goes dead.

## Dispositions

| Disposition | Meaning | Paths |
|---|---|---:|
| **move** | Relocated into SPY-DER with behavior preserved; the mathematics is not rewritten. | 12 |
| **reimplement** | Rebuilt behind a SPY-DER-owned interface because the 0DTE version is coupled to flat-module imports that cannot cross the boundary intact. Same parity gate. | 31 |
| **replace** | SPY-DER already owns a working implementation; the 0DTE module is superseded once parity passes. | 119 |
| **archive** | Kept read-only for provenance. Not carried into the runtime. | 139 |
| **delete** | Removed outright — a temporary bridge whose purpose ends at cutover, or dead code. | 27 |

`status` is `done` (26 rules), `partial` (21) or `pending` (19). **`done` means
the SPY-DER implementation exists, not that parity has been signed off** — that
is what `docs/CUTOVER_PLAN.md` step 4 is for.

## Headline matrix

| Capability | 0DTE implementation | SPY-DER implementation | Production-ready owner |
|---|---|---|---|
| Market feed | Existing live implementation (`massive_feed`, `tradier_feed`, `tastytrade_feed`, `yahoo_feed`) | `spy_der.market_data` — protocol, composite failover, assembler, freshness, calendar; live vendor adapters **pending** | Migrate to SPY-DER |
| Chain storage | `chain_store` | `spy_der.market_data.recording` / `.replay` (canonical snapshots) | Migrate |
| Features | `mtf_matrix`, `gex/`, `rnd_extractor`, `resample`, `market_dynamics` | `spy_der.features` — parity for MTF/RND/volatility; GEX and bar technicals partial | Validate and migrate |
| Forecasting | `prediction/` (57 modules, ~18k LOC), `mc`, `forecast_stabilizer` | `spy_der.forecasting` + `spy_der.training` with phase 5–9 parity fixtures | Validate and migrate |
| Candidates | `spread_selector`, `gate_scorer` | `spy_der.candidates` — geometry, payoff proofs, dominance | Validate and migrate |
| Risk | `risk_manager`, `execution_cost` | `spy_der.risk`, `spy_der.economics` | Consolidate |
| Decisions | Legacy bridge (`decision_engine`, `decision_matrix`, `policy/`) | `spy_der.decisions` + `spy_der.policies` | SPY-DER |
| Execution | `paper_broker`, `execution/` | `spy_der.execution` + **`spy_der.execution.guard`** | SPY-DER |
| Journal | `journal`, `journal_insights` | `spy_der.journal` (append-only, hash-chained, `snapshot_id`-keyed) | Migrate |
| Settlement | `journal` settlement records | `spy_der.evaluation.settlement`, outcome contracts | Migrate |
| Dashboard | `dashboard/` + Vercel UI (`api/`, `vercel.json`) | `spy_der.deployment.dashboard`, `spy_der.runtime.state_writer` | Port or reconnect |
| Synthetic universes | `matrix_universe`, `synthetic_world`, `regime_calibration` | **`spy_der.synthetic`** | SPY-DER ✅ |
| Dojo | Removed from 0DTE by PR #150 | `spy_der.dojo` | SPY-DER ✅ |

## Full ledger

Counts are tracked paths matched by each rule. Rules are evaluated in order and
the first match wins, so narrower patterns precede broader ones.

### Market data

| 0DTE path | Files | Disposition | SPY-DER owner | Status | Parity gate |
|---|---:|---|---|---|---|
| `massive_feed.py` | 1 | reimplement | `spy_der.market_data.providers.massive` | pending | raw_market_snapshots |
| `tradier_feed.py` | 1 | reimplement | `spy_der.market_data.providers.tradier` | pending | raw_market_snapshots |
| `tastytrade_feed.py` | 1 | reimplement | `spy_der.market_data.providers.tastytrade` | pending | raw_market_snapshots |
| `yahoo_feed.py` | 1 | reimplement | `spy_der.market_data.providers.yahoo` | pending | raw_market_snapshots |
| `composite_feed.py` | 1 | replace | `spy_der.market_data.composite` | done | raw_market_snapshots |
| `live_feed_adapter.py` | 1 | replace | `spy_der.market_data.assembler` + `.freshness` | done | raw_market_snapshots |
| `market_calendar.py` | 1 | replace | `spy_der.market_data.calendar` | done | raw_market_snapshots |
| `chain_store.py` | 1 | reimplement | `spy_der.market_data.recording` + `.replay` | partial | recorded_session_replay |

The four vendor adapters are the **largest remaining gap to independence**. Until
they land, SPY-DER's only live market source is the 0DTE bridge.

### Features and regime

| 0DTE path | Files | Disposition | SPY-DER owner | Status | Parity gate |
|---|---:|---|---|---|---|
| `resample.py` | 1 | reimplement | `spy_der.features.mtf` | partial | feature_values |
| `gex_window.py` | 1 | reimplement | `spy_der.features.gex` | partial | feature_values |
| `spy0dte.py` | 1 | reimplement | `spy_der.contracts.market` + `spy_der.features.gex` | partial | feature_values |
| `gex/**` | 7 | replace | `spy_der.features.gex` | partial | feature_values |
| `mtf_matrix.py` | 1 | replace | `spy_der.features.mtf` | done | feature_values |
| `rnd_extractor.py` | 1 | replace | `spy_der.features.rnd` | done | feature_values |
| `volatility_channel_features.py` | 1 | replace | `spy_der.features.volatility` | done | feature_values |
| `market_dynamics.py` | 1 | reimplement | `spy_der.features.structural` | partial | feature_values |
| `pin_regime.py` | 1 | replace | `spy_der.forecasting.regime_labels` | done | regime_labels |
| `regime_classifier.py` | 1 | replace | `spy_der.forecasting.regime_labels` | done | regime_labels |
| `regime_alignment.py` | 1 | reimplement | `spy_der.features.structural` | partial | regime_labels |

### Forecasting

| 0DTE path | Files | Disposition | SPY-DER owner | Status | Parity gate |
|---|---:|---|---|---|---|
| `prediction/**` | 57 | replace | `spy_der.forecasting` + `.training` + `.evaluation` | partial | forecast_outputs |
| `mc.py` | 1 | replace | `spy_der.forecasting.path_model` | done | forecast_outputs |
| `forecast_stabilizer.py` | 1 | reimplement | `spy_der.forecasting.uncertainty` | partial | forecast_outputs |

`prediction/**` is the single largest block. SPY-DER's System B implementations
already exist with parity fixtures for phases 5–9, so the remaining work is
**validating each against the 0DTE golden, not writing new code**. Working
mathematical code is not rewritten to look different.

### Candidates, risk, decisions

| 0DTE path | Files | Disposition | SPY-DER owner | Status | Parity gate |
|---|---:|---|---|---|---|
| `spread_selector.py` | 1 | replace | `spy_der.candidates.geometry` + `.factory` | done | candidate_sets |
| `gate_scorer.py` | 1 | replace | `spy_der.candidate_value` + `spy_der.risk.firewall` | done | candidate_sets |
| `risk_manager.py` | 1 | replace | `spy_der.risk` | done | risk_values |
| `execution_cost.py` | 1 | replace | `spy_der.economics` | done | risk_values |
| `execution/**` | 3 | replace | `spy_der.execution` | done | risk_values |
| `decision_engine.py` | 1 | replace | `spy_der.decisions` | done | vetoes |
| `decision_matrix.py` | 1 | replace | `spy_der.policies` | done | vetoes |
| `policy/**` | 5 | replace | `spy_der.policies` | done | vetoes |
| `paper_broker.py` | 1 | reimplement | `spy_der.execution.simulator` + `spy_der.positions` | partial | settlement |

### Journal, settlement, learning

| 0DTE path | Files | Disposition | SPY-DER owner | Status | Parity gate |
|---|---:|---|---|---|---|
| `journal.py` | 1 | reimplement | `spy_der.journal` + `spy_der.evaluation.settlement` | partial | journal_output |
| `journal_insights.py` | 1 | reimplement | `spy_der.evaluation.reports` + `spy_der.learning` | partial | journal_output |
| `optimizer.py` | 1 | reimplement | `spy_der.learning.optimization` | partial | none |
| `walk_forward.py` | 1 | reimplement | `spy_der.training.folds` | partial | none |
| `validation_pipeline.py` | 1 | reimplement | `spy_der.evaluation` + `spy_der.runtime.parity` | partial | none |
| `validation/**` | 3 | replace | `spy_der.training.folds` | done | none |
| `backtest.py` | 1 | reimplement | `spy_der.replay` | partial | recorded_session_replay |
| `acceptance.py` | 1 | replace | `tests/` | done | none |

SPY-DER's journal is an append-only hash-chained event store keyed by
`snapshot_id`; 0DTE's is a wide-row SQLite trade log. Behavior is held
equivalent; the schema deliberately is not.

### Synthetic universes — migrated

| 0DTE path | Files | Disposition | SPY-DER owner | Status | Parity gate |
|---|---:|---|---|---|---|
| `matrix_universe.py` | 1 | move | `spy_der.synthetic.archetypes` / `.chains` / `.world` / `.universe` | **done** | synthetic_world_parity |
| `synthetic_world.py` | 1 | move | `spy_der.synthetic.world` + `.pricing` | **done** | synthetic_world_parity |
| `regime_calibration.py` | 1 | move | `spy_der.synthetic.calibration` | **done** | synthetic_world_parity |

Generative constants are preserved verbatim and fingerprinted by
`simulator_config_hash()`. `matrix_universe`'s archetype catalog, Markov world
generation, coupled chain repricing, coverage matrix, regime calibration and
weak-archetype evolution all have native homes. The Dojo calls
`spy_der.synthetic.SyntheticUniverseProvider`, so
`integrations/spy_der/synthetic.py` in 0DTE is already dead.

### Runtime and orchestration

| 0DTE path | Files | Disposition | SPY-DER owner | Status | Parity gate |
|---|---:|---|---|---|---|
| `unified_loop.py` | 1 | replace | `spy_der.runtime.runner` + `.ai_loop` + `spy_der.market_data` | partial | live_shadow_parity |
| `shadow_runner.py` | 1 | replace | `spy_der.runtime.ai_loop` + `.runner` | done | live_shadow_parity |
| `orchestrator.py` | 1 | replace | `spy_der.runtime.runner` | done | none |
| `notifier.py` | 1 | move | `spy_der.deployment.notifications` | partial | none |
| `config_loader.py` | 1 | reimplement | `spy_der.deployment.manifest` | partial | none |
| `configs/**` | 5 | reimplement | `/etc/spy-der/config.yaml` | pending | none |
| `deploy/**` | 25 | replace | `deploy/spy-der-*.service`, `*.timer` | partial | none |

`unified_loop.py` (2051 lines) is why most 0DTE modules cannot be moved intact —
nearly everything imports it. It is decomposed, not ported.

### Dashboard

| 0DTE path | Files | Disposition | SPY-DER owner | Status | Parity gate |
|---|---:|---|---|---|---|
| `dashboard/static/**` | 3 | move | `apps/dashboard` | pending | dashboard_compatibility |
| `dashboard/**` | 7 | reimplement | `services/dashboard_api` | pending | dashboard_compatibility |
| `api/**` | 1 | move | `apps/dashboard` | pending | dashboard_compatibility |
| `vercel.json`, `.vercelignore`, `.env.vercel.example`, `package.json` | 4 | move | `apps/dashboard` | pending | dashboard_compatibility |

The chosen approach is **keep the Vercel frontend, move only its data source**:

```
Vercel dashboard
       |
  SPY-DER API
```

The response schema is held compatible so the frontend needs no change at
cutover. Frontend assets and Vercel config move to `apps/dashboard`; the backend
is reimplemented as `services/dashboard_api` reading SPY-DER state directly.

### Bridge — deleted at cutover

| 0DTE path | Files | Disposition | Status |
|---|---:|---|---|
| `zerodte/**` | 14 | delete | pending |
| `integrations/**` | 10 | delete | pending |
| `.github/**` | 3 | delete | pending |

`zerodte/**` and `integrations/**` are PR #150's contract bridge. Their entire
purpose ends at cutover; nothing is carried forward. The SPY-DER-side mirror,
`spy_der.integrations.zerodte`, is likewise a re-export-only shim removed at
cutover step 10.

### Archived for provenance

| 0DTE path | Files | Disposition | Status |
|---|---:|---|---|
| `tests/**` | 121 | archive | partial |
| `docs/**` | 10 | archive | pending |
| `scripts/**` | 4 | archive | pending |
| `AGENTS.md`, `HANDOFF.md`, `requirements.txt`, `.gitignore` | 4 | archive | mixed |

Tests covering migrated mathematics move with their subject and become SPY-DER
parity fixtures; tests of deleted bridge code are dropped. `requirements.txt` is
already captured verbatim at `baseline/manifests/system_a_requirements.txt`.
