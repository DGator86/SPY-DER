from __future__ import annotations

from datetime import datetime
from typing import Protocol

from system_b.contracts import (
    Candidate,
    CandidateForecast,
    CandidateUniverse,
    CanonicalMarketSnapshot,
    FeatureBundle,
    JournalEvent,
    LegacyDecisionView,
    MarketForecastBundle,
    OrderIntent,
    OrderState,
    PositionState,
    RiskDecision,
    RiskEnvelope,
    SystemDecision,
)


class MarketDataProvider(Protocol):
    def get_snapshot(self, timestamp: datetime) -> CanonicalMarketSnapshot: ...


class FeaturePipeline(Protocol):
    def build(self, snapshot: CanonicalMarketSnapshot) -> FeatureBundle: ...


class StructuralAnalyzer(Protocol):
    def analyze(self, features: FeatureBundle) -> LegacyDecisionView: ...


class MarketForecaster(Protocol):
    def forecast(self, features: FeatureBundle) -> MarketForecastBundle: ...


class CandidateFactory(Protocol):
    def generate(self, snapshot: CanonicalMarketSnapshot) -> CandidateUniverse: ...


class CandidateValueModel(Protocol):
    def score(self, candidate: Candidate, forecast: MarketForecastBundle) -> CandidateForecast: ...


class DecisionSynthesizer(Protocol):
    def synthesize(
        self,
        legacy: LegacyDecisionView,
        forecast: MarketForecastBundle,
        universe: CandidateUniverse,
    ) -> SystemDecision: ...


class RiskFirewall(Protocol):
    def evaluate(
        self,
        decision: SystemDecision,
        envelope: RiskEnvelope,
        universe: CandidateUniverse,
    ) -> RiskDecision: ...


class ExecutionSimulator(Protocol):
    def route(self, intent: OrderIntent) -> OrderState: ...


class PositionManager(Protocol):
    def update(
        self,
        order_state: OrderState,
        position_state: PositionState | None,
    ) -> PositionState: ...


class JournalStore(Protocol):
    def append(self, event: JournalEvent) -> None: ...


class ReplayEngine(Protocol):
    def replay(
        self,
        adapter: SystemAdapter,
        inputs: ReplayInputManifest,
    ) -> tuple[JournalEvent, ...]: ...


class SystemAdapter(Protocol):
    name: str
    version: str

    def run(self, inputs: ReplayInputManifest) -> tuple[JournalEvent, ...]: ...


class ReplayInputManifest(Protocol):
    timestamp: str
    market_snapshot_hash: str
    underlying_bars_hash: str
    option_chain_hash: str
    candidate_universe_hash: str
    fees_hash: str
    slippage_hash: str
    fill_assumptions_hash: str
    account_size_hash: str
    risk_ceilings_hash: str
    exit_policy_hash: str
    settlement_hash: str
