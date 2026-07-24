# CONTRACT CATALOG

Canonical contracts currently defined:

- CanonicalMarketSnapshot
- FeatureBundle
- StructuralState
- StrategyPermissions
- HardVeto
- LegacyDecisionView
- MarketForecastBundle
- Candidate / OptionLeg / CandidateUniverse
- CandidateForecast / CandidateRanking / V3DecisionView
- SystemDecision / RiskEnvelope / RiskDecision
- OrderIntent / OrderState / PositionState / ExitPolicy
- OutcomeRecord / JournalEvent
- DeploymentManifest
- SystemAdapter

## Cross-repository integration contracts

| Schema | Direction | Type |
|---|---|---|
| `zerodte.spyder.market.v1` | 0DTE → SPY-DER | `MarketPacket` |
| `zerodte.spyder.outcome.v1` | 0DTE → SPY-DER | `OutcomePacket` |
| `spyder.dashboard.v1` | SPY-DER → 0DTE | `DashboardPacket` |
| `spyder.decision.request.v1` | 0DTE → SPY-DER HTTP | `DecisionRequest` |
| `spyder.decision.response.v1` | SPY-DER → 0DTE HTTP | `DecisionResponse` |

Module: `spy_der.contracts.integration`.
