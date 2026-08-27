# Alpha V2 Migration Audit

Status: implementation audit against the market-model-first architecture

This audit classifies the current SPY-DER code by its proper role in Alpha V2. The objective is to preserve useful machinery while removing architectural ambiguity.

## Executive conclusion

SPY-DER does not need to be rewritten from zero.

The current repository already contains substantial reusable infrastructure:

- point-in-time market snapshots;
- options/dealer structural evidence;
- fail-closed forecast serving;
- calibrated probabilistic models;
- return quantiles;
- volatility forecasts;
- range survival;
- barrier/touch forecasts;
- path forecasts;
- ensemble weighting;
- uncertainty and OOD machinery;
- candidate generation;
- executable economics;
- deterministic risk controls;
- execution / position / journal infrastructure.

The main problem is **role ambiguity**, not absence of capability. Several objects named `StructuralState` or `regime` currently mix different conceptual layers. Alpha V2 resolves this by placing each existing component behind an explicit boundary.

## Component classification

### `contracts/market.py`

**Alpha V2 role:** raw observation / canonical snapshot boundary.

**Action:** preserve.

This remains the source point-in-time market snapshot from which measurements are derived.

---

### `contracts/structure.py`

Current contents include:

- OI-based GEX;
- gamma flip;
- call / put walls;
- concentration;
- ATM straddle / expected move;
- risk-neutral-density summary.

**Alpha V2 role:** Observation Engine evidence, primarily the `options_dealer` state family and part of market-implied Q.

**Action:** preserve calculations, reclassify semantics.

This object is not the full Alpha V2 market state. Gamma and option structure are sensors analogous to atmospheric pressure or radar. They contribute to market state but cannot define the entire market regime by themselves.

The RND summary must remain explicitly market-implied / risk-neutral. It is evidence about Q, not the physical distribution P.

---

### `contracts/models.py::FeatureBundle`

**Alpha V2 role:** legacy compatibility feature container.

**Action:** migrate callers toward `MeasurementBundle`.

The current `tuple[(name, float)]` representation is too weak for the new Observation Engine because it does not carry:

- canonical variable IDs;
- explicit missingness;
- source IDs;
- quality;
- normalization state;
- dictionary version.

`MeasurementBundle` becomes the new canonical upstream contract while `FeatureBundle` remains available during migration.

---

### Duplicate `StructuralState` contracts

There are currently at least two different concepts using this name:

1. `contracts/structure.py::StructuralState`: rich GEX / vol / RND evidence;
2. `contracts/models.py::StructuralState`: lightweight state ID / regime / evidence compatibility object.

**Alpha V2 role:** ambiguous legacy names.

**Action:** do not add another object with the same name. Use the explicit new `MarketState` contract for the whole-market continuous state. Gradually retire or namespace the two legacy meanings after call sites are migrated.

---

### `legacy/analyzer.py`

The analyzer maps gamma evidence into:

- preferred direction;
- permitted option families;
- prohibited option families;
- structural confidence;
- size cap;
- hard vetoes.

**Alpha V2 role:** Trader / legacy policy adapter.

**Action:** preserve downstream for compatibility, but prohibit it from the Market Model and physical forecast P.

The name `LegacyAnalyzer` is acceptable because the code is useful for policy parity. Its output is not a market state. It is a trading interpretation of selected market evidence.

---

### `forecasting/runtime.py`

**Alpha V2 role:** reusable Forecast Engine serving infrastructure.

**Action:** preserve and adapt input contract.

Strengths already present:

- registered model groups;
- model status / mode checks;
- fail-closed missing required inputs;
- no candidate object required to generate the market forecast;
- separate direction, return, volatility, range and touch heads;
- artifact hashes and versions.

Required migration:

1. replace untyped `feature_row` assembly upstream with a projection from the immutable `MeasurementBundle` + `MarketState` + `RegimePosterior`;
2. record market-state / regime / calibration version IDs in the forecast bundle;
3. expand supported lifecycle / path outputs without introducing candidate information;
4. retain missing values as missing.

The research-only `heuristic_bundle` must remain research/shadow-only. It cannot become a production fallback that manufactures confidence from missing evidence.

---

### `forecasting/models/regime_moe.py`

Strengths already present:

- full multiclass probability vector;
- calibrated probabilities;
- normalized probability sum;
- entropy-style uncertainty;
- dominant regime only as a convenience;
- session-aware inner folds / embargo machinery;
- baseline / HGB alternatives.

**Alpha V2 role:** reusable probabilistic regime-model machinery.

**Problem:** the current label vocabulary is mostly gamma-centric:

- `long_gamma_pin`;
- `short_gamma_trend`;
- `flip_transition`;
- `volatility_expansion`.

**Action:** preserve the modeling machinery, but treat the current labels as a dealer/options substate model. Train the canonical Alpha V2 regime posterior on the broader `MarketState` atmosphere.

The existing dealer-regime posterior may itself become one input to the broader regime model.

---

### `forecasting/ensemble.py`

**Alpha V2 role:** Forecast Engine ensemble / model-witness layer.

**Action:** preserve.

Its use of historical out-of-sample loss, component caps, explicit missing components, disagreement and uncertainty is directionally correct.

HGB, Beta and other forecasting components belong here or immediately upstream as independent witnesses. They must not directly select option structures.

---

### `forecasting/v3.py`

**Alpha V2 role:** reusable physical-forecast extension assembly.

**Action:** preserve and progressively type.

Existing useful extension families include:

- uncertainty components;
- OOD support;
- regime probabilities;
- competing risks;
- path forecasts;
- ensemble forecasts;
- return distributions.

These align closely with the new architecture. The primary migration is to reference the new `RegimePosterior`, `RegimeLifecycleForecast`, and `MarketState` identities rather than treating all regime/state information as loose extension dictionaries.

---

### `synthesis/engine.py`

The synthesis layer receives:

- legacy trading interpretation;
- market forecast;
- candidate universe;
- V3 decision view;
- risk envelope.

**Alpha V2 role:** Trader / policy synthesis.

**Action:** preserve downstream.

This location is appropriate for bringing trading policy and candidates back together, provided the `MarketForecastBundle` was frozen before this stage.

---

### Candidate / value / economics / risk / execution / positions

**Alpha V2 role:** Trader and execution stack.

**Action:** preserve unless later economic validation identifies specific defects.

These layers should never become dependencies of Observation, Market State, Regime, or physical P generation.

## New canonical interfaces

The branch adds explicit interfaces for:

1. `ObservationEngine`
2. `MarketStateEngine`
3. `RegimeInferenceEngine`
4. `RegimeLifecycleEngine`
5. `PhysicalMarketForecaster`
6. `MarketForecastVerifier`

The old `FeaturePipeline`, `StructuralAnalyzer`, and `MarketForecaster` interfaces remain temporarily as compatibility surfaces. `StructuralAnalyzer` is now documented as a legacy Trader/policy adapter rather than the market-state engine.

## Migration sequence

### Migration 1 — Observation projection

Build an adapter:

```text
CanonicalMarketSnapshot
    + existing structural evidence
    + breadth / constituent / technical / cross-asset features
    -> MeasurementBundle
```

The UTPM variable dictionary becomes the source naming/version registry. Decision and Output modules are excluded from the Observation Engine.

### Migration 2 — market state

Build the 11-axis `MarketState` from measurement groups. Initial implementations can be transparent weighted composites or calibrated latent factors; the contract is algorithm-independent.

### Migration 3 — regime posterior

Retarget the existing calibrated multiclass machinery to a broader market-regime label set. Preserve dealer/gamma regime as a separate submodel / input.

### Migration 4 — lifecycle

Move persistence, transition and transition-time models behind `RegimeLifecycleForecast`.

Persistence and successor destination remain separate model heads and separate validation objectives.

### Migration 5 — physical forecast

Adapt current direction / return / volatility / range / touch / path heads to consume the standardized market-model inputs and freeze a complete P object.

### Migration 6 — verification tape

Mature every forecast independently of trading and emit `MarketForecastVerification` records.

No trade is required for a forecast to enter calibration statistics.

### Migration 7 — Trader P/Q boundary

Only after P is frozen:

```text
P + Q + executable option market + risk constraints
    -> WAIT / NO_EDGE / ABSTAIN / TRADE
```

Legacy gamma-based family permissions can remain as one downstream policy witness while their real incremental value is tested against the new P/Q valuation framework.

## Immediate defects identified

### Silent directional fallback

The legacy convenience property on `MarketForecastBundle` returned `0.0` when every directional forecast was missing. Its complement therefore appeared to be `1.0` down probability.

That violates fail-closed semantics. The Alpha V2 branch changes both aliases to preserve `None` when direction is unavailable.

### Role collision around `regime`

The repository currently uses regime language for:

- dealer gamma state;
- legacy policy state;
- V3 regime probabilities;
- structural evidence.

Alpha V2 requires separate names / IDs for:

- measurement substate;
- whole-market state;
- whole-market regime posterior;
- lifecycle forecast;
- Trader policy interpretation.

## Non-negotiable migration tests

Before a migrated component is promoted, tests must prove:

1. candidate geometry cannot enter P;
2. candidate rank cannot enter P;
3. selected action cannot enter P;
4. order / fill / position / P&L cannot enter P;
5. missing sensor values remain missing unless an explicitly versioned imputation model is used;
6. normalization statistics are training-fold/version specific;
7. the full regime posterior sums to one;
8. survival probability cannot increase with forecast horizon;
9. replaying identical frozen inputs reproduces identical measurement, state, regime and forecast IDs;
10. forecast verification occurs without a trade;
11. Trader outcomes cannot mutate already-recorded physical forecasts.

## Bottom line

Keep the engineering infrastructure. Change the ontology and the boundaries.

The repository already contains many of the instruments Alpha V2 needs. The reset is about making them answer the correct questions in the correct order.