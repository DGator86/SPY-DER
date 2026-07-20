# MIGRATION MAP (source-validated)

Source-validated migration map for SPY-DER, produced during **Phase 0 — Source access
and baseline** (`docs/SPY_DER_MASTER_SPEC.md` §62, §63, §71). It replaces the earlier
empty provisional map and supersedes the provisional map embedded in spec §62.

- **Source (System A):** `DGator86/0DTE` @ `de4a6e7ced98ff97c778e8b4418c08848d7ce82d`
- **Target (System B):** `DGator86/SPY-DER`, package `src/spy_der/` (spec §10)
- **Baseline lock:** [`baseline/system_a.lock.json`](../baseline/system_a.lock.json)

### What "validated" means here

Every **source** path below was confirmed to exist in the pinned System A tree
(`baseline/manifests/system_a_tree.txt`). No code has been migrated. **Target** paths are
planned destinations only; the *correctness* of each mapping is proven per-phase by parity
tests (spec §65), not asserted here. No parity, performance, or "migrated" claim is made
(spec §5, §67).

**Status legend**
- ✅ **CONFIRMED** — mapping present in spec §62 provisional map; source path verified to exist.
- ➕ **ADDED** — real source present at the pin but **absent from the §62 provisional map**;
  added here so the map is complete against the tree.

---

## 1. Legacy structural layer → `src/spy_der/legacy/`, `features/`, `risk/`

| Source (System A) | Planned target (System B) | Status |
|---|---|---|
| `gate_scorer.py` | `legacy/gates.py`, `legacy/permissions.py`, `risk/lockout.py` | ✅ |
| `decision_engine.py` | `legacy/adapter.py`, `synthesis/deterministic.py` | ✅ |
| `decision_matrix.py` | `legacy/regime.py`, `legacy/analyzer.py` | ✅ |
| `regime_classifier.py` | `legacy/regime.py` | ✅ |
| `regime_alignment.py` | `legacy/analyzer.py`, `positions/exits.py` | ✅ |
| `pin_regime.py` | `legacy/regime.py` (pin-regime detection) | ➕ |
| `policy/legacy_matrix.py` | `legacy/analyzer.py` / `policies/legacy.py` | ➕ |

## 2. Features → `src/spy_der/features/`

| Source (System A) | Planned target (System B) | Status |
|---|---|---|
| `rnd_extractor.py` | `features/rnd.py`, `forecasting/physical_distribution.py` (physical-pdf portion) | ✅ |
| `resample.py` | `features/mtf.py` | ✅ |
| `mtf_matrix.py` | `features/mtf.py`, `features/normalization.py` | ✅ |
| `gex_window.py` | `features/gex.py` (persistent \|GEX\|-rank state) | ✅ |
| `market_dynamics.py` | `features/dynamics.py` | ✅ |
| `gex/base.py`, `gex/oi.py`, `gex/volume_proxy.py`, `gex/hybrid.py`, `gex/weekly.py`, `gex/contracts.py` | `features/gex.py` (OI / volume-proxy / hybrid / weekly variants; §62 mapped only `gex_window.py`) | ➕ |
| `volatility_channel_features.py` | `features/volatility.py` | ➕ |
| `prediction/scalers.py` | `features/normalization.py` | ✅ |
| `prediction/structural_state.py` | `contracts/structure.py`, `features/service.py` | ✅ |

## 3. Market data / calendar / replay → `src/spy_der/market_data/`, `replay/`

| Source (System A) | Planned target (System B) | Status |
|---|---|---|
| `composite_feed.py` | `market_data/assembler.py` | ✅ |
| `tradier_feed.py`, `tastytrade_feed.py`, `massive_feed.py`, `yahoo_feed.py`, `live_feed_adapter.py` | `market_data/providers/` | ✅ (provider modules) |
| `chain_store.py` | `market_data/recording.py`, `market_data/replay.py` | ✅ |
| `market_calendar.py` | `market_data/calendar.py` | ✅ |
| `synthetic_world.py` | `replay/synthetic.py` | ✅ |
| `backtest.py` | `replay/engine.py` | ✅ |
| `prediction/canonical_snapshot.py`, `prediction/feed_status.py` | `market_data/snapshot.py` (canonical snapshot + feed provenance) | ➕ |

## 4. Forecasting (V2/V3) → `src/spy_der/forecasting/`

| Source (System A) | Planned target (System B) | Status |
|---|---|---|
| `prediction/contracts.py` | `contracts/forecasts.py` | ✅ |
| `prediction/inference.py` | `forecasting/runtime.py` | ✅ |
| `prediction/uncertainty.py` | `forecasting/uncertainty.py` | ✅ |
| `prediction/ood.py` | `forecasting/ood.py` | ✅ |
| `prediction/path_model.py` | `forecasting/path_model.py` | ✅ |
| `prediction/ensemble.py` | `forecasting/ensemble.py` | ✅ |
| `mc.py` | `forecasting/` path baseline (labeled fallback, spec §29) | ✅ |
| `prediction/models/direction.py` | `forecasting/models/direction.py` | ✅ |
| `prediction/models/return_quantiles.py` | `forecasting/models/return_quantiles.py` | ✅ |
| `prediction/models/volatility.py` | `forecasting/models/volatility.py` | ✅ |
| `prediction/models/range_survival.py` | `forecasting/models/range_survival.py` | ✅ |
| `prediction/models/barrier_touch.py` | `forecasting/models/barrier_touch.py` | ✅ |
| `prediction/models/regime_moe.py` | `forecasting/models/regime.py` | ✅ |
| `prediction/models/mixture_experts.py` | `forecasting/models/mixture_experts.py` | ✅ |
| `prediction/models/competing_risk.py` | `forecasting/models/competing_risk.py` | ✅ |
| `prediction/part2_shadow.py` | forecast shadow adapter | ✅ |
| `prediction/physical_distribution.py` | `forecasting/physical_distribution.py` (primary source; pairs with `rnd_extractor`) | ➕ |
| `prediction/path_model_v3.py` | `forecasting/path_model.py` (V3 variant) | ➕ |
| `prediction/conformal.py` | `forecasting/conformal.py` (conformal/competing-risk, spec §28) | ➕ |
| `prediction/return_distribution.py`, `prediction/sigma_cone.py` | `forecasting/` distribution/interval outputs | ➕ |
| `prediction/models/base.py` | `forecasting/models/base.py` | ➕ |
| `prediction/v3_part2_config.py` | `configs/models/` + `forecasting/runtime.py` config | ➕ |

## 5. Candidate factory → `src/spy_der/candidates/`

| Source (System A) | Planned target (System B) | Status |
|---|---|---|
| `spread_selector.py` | `candidates/factory.py`, `candidates/payoff.py`, `economics/service.py` | ✅ |
| `zerodte/contracts/candidates.py` | `contracts/candidates.py` (canonical candidate contract, see §11) | ➕ |

## 6. Economics & candidate value → `src/spy_der/economics/`, `candidate_value/`

| Source (System A) | Planned target (System B) | Status |
|---|---|---|
| `execution/fill_records.py` | `execution/fill_records.py` | ✅ |
| `prediction/models/fill_probability.py` | `economics/models/fill_probability.py` | ✅ |
| `prediction/models/fill_concession.py` | `economics/models/fill_concession.py` | ✅ |
| `prediction/models/candidate_value.py` | `candidate_value/models/value.py` | ✅ |
| `prediction/models/candidate_rank.py` | `candidate_value/models/ranking.py` | ✅ |
| `prediction/models/trade_meta.py` | `candidate_value/models/meta.py` | ✅ |
| `execution_cost.py` | `economics/service.py` (fees/slippage/execution cost) | ➕ |
| `execution/estimate_v3.py` | `economics/service.py` (V3 executable estimate) | ➕ |
| `prediction/models/fill.py`, `prediction/fill_training.py` | `economics/models/` (fill model + training) | ➕ |
| `prediction/candidate_ranker.py` | `candidate_value/ranking.py` (ranker runtime) | ➕ |

## 7. Policies & synthesis → `src/spy_der/policies/`, `synthesis/`

| Source (System A) | Planned target (System B) | Status |
|---|---|---|
| `policy/contracts.py` | `contracts/policies.py` | ✅ |
| `policy/prediction_policy.py` | `policies/v2.py` | ✅ |
| `prediction/part3_shadow.py` | candidate-value policy adapter | ✅ |
| `policy/router.py` | `synthesis/` policy routing / `policies/ensemble.py` | ➕ |
| `zerodte/agent/contracts.py`, `zerodte/agent/runtime.py` | `synthesis/agents/` (provider-neutral AI layer, spec §37) | ➕ |

## 8. Risk → `src/spy_der/risk/`

| Source (System A) | Planned target (System B) | Status |
|---|---|---|
| `risk_manager.py` | `risk/firewall.py`, `risk/portfolio.py`, `risk/sizing.py` | ✅ |
| `zerodte/contracts/risk.py` | `contracts/risk.py` | ✅ |
| `gate_scorer.py` lockouts | `risk/lockout.py` | ✅ |
| `spy0dte.py` `scale_risk` | `risk/sizing.py` | ✅ |

## 9. Execution & positions → `src/spy_der/execution/`, `positions/`

| Source (System A) | Planned target (System B) | Status |
|---|---|---|
| `paper_broker.py` | `execution/`, `positions/` | ✅ |
| order/position enums (spec §51-§52) | `contracts/execution.py`, `contracts/positions.py` | ✅ |
| fill simulator / accounts / restart | `execution/simulator.py`, `accounts.py`, `positions/*` | ✅ |

## 10. Journal, evaluation, training, deployment → `journal/`, `evaluation/`, `training/`, `deployment/`

| Source (System A) | Planned target (System B) | Status |
|---|---|---|
| `journal.py` | `journal/store.py`, `journal/projections.py`, `evaluation/counterfactuals.py` | ✅ |
| `walk_forward.py` | `training/folds.py`, `evaluation/` | ✅ |
| `optimizer.py` | `training/experiments.py` | ✅ |
| `validation_pipeline.py` | `evaluation/validation.py`, `deployment/drift.py` | ✅ |
| `prediction/storage.py` | journal + research storage | ✅ |
| `prediction/dataset.py` | `training/datasets.py` | ✅ |
| `prediction/candidate_dataset.py` | training candidate datasets | ✅ |
| `prediction/labels.py` | `evaluation/labels.py` | ✅ |
| `prediction/training.py` | `training/pipelines.py` | ✅ |
| `prediction/calibration.py` | `training/calibration.py` | ✅ |
| `prediction/crossfit.py` | `training/folds.py` | ✅ |
| `prediction/session_bootstrap.py` | `evaluation/bootstrap.py` | ✅ |
| `prediction/regime_labels.py` | `evaluation/labels.py` | ✅ |
| `prediction/event_dataset.py` | `training/datasets.py` | ✅ |
| `prediction/dynamic_weights.py` | `deployment/weights.py` | ✅ |
| `prediction/drift.py` | `deployment/drift.py` | ✅ |
| `prediction/deployment.py` | `deployment/manifest.py`, `deployment/rollback.py` | ✅ |
| `prediction/promotion.py` | `deployment/promotion.py` | ✅ |
| `prediction/registry.py` | `training/registry.py` | ✅ |
| `prediction/asof.py` | `training/datasets.py` (as-of assembly) | ➕ |
| `validation/session_folds.py`, `validation/bootstrap.py` | `evaluation/bootstrap.py`, `training/folds.py` | ➕ |
| `prediction/reports/part2_evaluation.py`, `part3_evaluation.py`, `promotion_packet.py` | `evaluation/reports/`, `deployment/promotion.py` | ➕ |
| `adaptive_learning/*` (learner, hypothesis, feature_lab, promoter, stability, diagnostics, config_store, reports) | `training/experiments.py`, `deployment/` (offline learning subsystem) | ➕ |

## 11. Runtime, contracts, canonical pipeline → `src/spy_der/runtime/`, `contracts/`

| Source (System A) | Planned target (System B) | Status |
|---|---|---|
| `unified_loop.py` | `runtime/pipeline.py` | ✅ |
| `shadow_runner.py` | `runtime/runner.py` | ✅ |
| `orchestrator.py` | `runtime/` (Track-A harness) | ➕ |
| `spy0dte.py` | `runtime/` (entry-point wiring) | ➕ |
| `config_loader.py` | `runtime/config.py` + `configs/` loader | ➕ |
| `zerodte/runtime/pipeline.py`, `zerodte/runtime/services.py` | `runtime/pipeline.py` (canonical sequencing antecedent) | ➕ |
| `zerodte/contracts/market.py`, `decisions.py` | `contracts/market.py`, `contracts/decisions.py` | ➕ |
| `zerodte/adapters/legacy_snapshot.py` | `market_data/` legacy snapshot adapter | ➕ |

> **`zerodte/` is the strongest migration anchor.** `AGENTS.md` documents System A's own
> in-progress unification into `zerodte/{contracts,runtime,agent,adapters}`, mirroring
> SPY-DER's target layout (spec §10). Phases building `contracts/`, `runtime/`, and the AI
> layer should start from these modules, not from scratch. See
> `docs/CURRENT_SYSTEM_INVENTORY.md` §7.

## 12. Dashboard & operations → `src/spy_der/dashboard/`, docs/ops

| Source (System A) | Planned target (System B) | Status |
|---|---|---|
| `dashboard/*` (server, state, queries, live_schema, auth, static) | `dashboard/` projections + canonical state | ✅ (dashboard modules) |
| `notifier.py` | `deployment/` notifications / operations | ➕ |
| `scripts/feature_impact.py`, `replay_ras.py`, `turn_lag_study.py` | `scripts/` | ➕ |
| `acceptance.py` | `tests/` acceptance / `scripts/` | ➕ |

---

## 13. Coverage check

- **§62 provisional-map source paths:** all present in the pinned tree (0 missing, 0 fabricated).
- **Real source added beyond §62 (➕):** the `gex/` package, the `zerodte/` unification
  package, `adaptive_learning/`, `validation/`, `policy/{legacy_matrix,router}.py`,
  `execution/estimate_v3.py`, `execution_cost.py`, `pin_regime.py`,
  `volatility_channel_features.py`, `config_loader.py`, `orchestrator.py`, `spy0dte.py`,
  `notifier.py`, `acceptance.py`, and several `prediction/` modules
  (`physical_distribution.py`, `path_model_v3.py`, `conformal.py`, `canonical_snapshot.py`,
  `feed_status.py`, `asof.py`, `candidate_ranker.py`, `return_distribution.py`,
  `sigma_cone.py`, `fill_training.py`, `v3_part2_config.py`, `reports/*`, `models/base.py`,
  `models/fill.py`).
- **Deferred:** pure config/deploy/proxy assets (`configs/`, `deploy/`, `api/[...path].js`,
  `vercel.json`, `package.json`) migrate as part of the phases that consume them, not as
  standalone code.

This map is validated for **source existence** against commit
`de4a6e7ced98ff97c778e8b4418c08848d7ce82d`. Each row's mapping is confirmed *behaviorally*
only when its phase lands with passing parity tests (spec §65).
