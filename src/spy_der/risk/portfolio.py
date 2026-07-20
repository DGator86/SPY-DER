"""Session-scoped portfolio limits and state tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from spy_der.contracts.common import content_hash
from spy_der.contracts.risk import PortfolioState, RiskLimits

__all__ = [
    "OpenPosition",
    "PortfolioTracker",
    "build_portfolio_state",
]


@dataclass(frozen=True, slots=True)
class OpenPosition:
    candidate_id: str
    family: str
    expiration: str
    max_loss: Decimal
    gamma: Decimal = Decimal("0")
    delta: Decimal = Decimal("0")
    geometry_hash: str = ""
    contracts: int = 1


@dataclass
class PortfolioTracker:
    """Intraday portfolio guard (System A RiskManager semantics).

    check()/record_trade() are two-phase: check is read-only; record mutates.
    State resets when session_date changes.
    """

    limits: RiskLimits = field(default_factory=RiskLimits)
    _session: str = ""
    _positions: list[OpenPosition] = field(default_factory=list)
    _daily_committed_loss: Decimal = Decimal("0")
    _daily_realized_pnl: Decimal = Decimal("0")

    def check(self, candidate: Any, session_date: str) -> tuple[bool, tuple[str, ...]]:
        self._maybe_reset(session_date)
        vetoes: list[str] = []
        max_loss = Decimal(str(getattr(candidate, "max_loss", None)
                               or getattr(candidate, "maximum_loss", 0) or 0))
        gamma = abs(Decimal(str(getattr(candidate, "gamma", 0) or 0)))
        family = str(getattr(candidate, "family", "") or "")
        expiration = str(getattr(candidate, "expiration", "") or "")

        if self.limits.max_daily_loss > 0:
            projected = self._daily_committed_loss + max_loss
            if projected > self.limits.max_daily_loss:
                vetoes.append(f"daily_loss:{projected}>{self.limits.max_daily_loss}")

        if self.limits.max_positions > 0 and len(self._positions) >= self.limits.max_positions:
            vetoes.append(
                f"max_positions:{len(self._positions)}>={self.limits.max_positions}"
            )

        if self.limits.gamma_limit is not None and self.limits.gamma_limit > 0:
            net_g = sum((p.gamma for p in self._positions), Decimal("0")) + gamma
            if net_g > self.limits.gamma_limit:
                vetoes.append(f"max_gamma:{net_g}>{self.limits.gamma_limit}")

        if self.limits.max_family_positions > 0 and family:
            count = sum(1 for p in self._positions if p.family == family) + 1
            if count > self.limits.max_family_positions:
                vetoes.append(
                    f"family_concentration:{family}:{count}>{self.limits.max_family_positions}"
                )

        if self.limits.max_expiration_positions > 0 and expiration:
            count = sum(1 for p in self._positions if p.expiration == expiration) + 1
            if count > self.limits.max_expiration_positions:
                vetoes.append(
                    f"expiration_concentration:{expiration}:{count}"
                    f">{self.limits.max_expiration_positions}"
                )

        return (not vetoes, tuple(vetoes))

    def record_trade(self, candidate: Any, session_date: str, contracts: int = 1) -> None:
        self._maybe_reset(session_date)
        max_loss = Decimal(str(getattr(candidate, "max_loss", None)
                               or getattr(candidate, "maximum_loss", 0) or 0))
        pos = OpenPosition(
            candidate_id=str(getattr(candidate, "candidate_id", "") or ""),
            family=str(getattr(candidate, "family", "") or ""),
            expiration=str(getattr(candidate, "expiration", "") or ""),
            max_loss=max_loss,
            gamma=abs(Decimal(str(getattr(candidate, "gamma", 0) or 0))),
            delta=Decimal(str(getattr(candidate, "delta", 0) or 0)),
            geometry_hash=str(getattr(candidate, "geometry_hash", "") or ""),
            contracts=max(1, int(contracts)),
        )
        self._positions.append(pos)
        self._daily_committed_loss += max_loss * pos.contracts

    def close_positions(self) -> None:
        self._positions.clear()

    def record_realized_pnl(self, pnl: Decimal, session_date: str) -> None:
        self._maybe_reset(session_date)
        self._daily_realized_pnl += Decimal(str(pnl))

    def status(self) -> dict[str, object]:
        return {
            "session_date": self._session,
            "open_positions": len(self._positions),
            "daily_loss_committed": str(self._daily_committed_loss),
            "daily_realized_pnl": str(self._daily_realized_pnl),
            "net_gamma": str(sum((p.gamma for p in self._positions), Decimal("0"))),
            "families": [p.family for p in self._positions],
        }

    def to_portfolio_state(
        self,
        *,
        account_id: str,
        equity: Decimal,
        cash: Decimal,
    ) -> PortfolioState:
        family_counts: dict[str, int] = {}
        expiration_counts: dict[str, int] = {}
        for pos in self._positions:
            if pos.family:
                family_counts[pos.family] = family_counts.get(pos.family, 0) + 1
            if pos.expiration:
                expiration_counts[pos.expiration] = (
                    expiration_counts.get(pos.expiration, 0) + 1
                )
        state_id = content_hash(
            {
                "account_id": account_id,
                "session": self._session,
                "open": [p.candidate_id for p in self._positions],
                "committed": str(self._daily_committed_loss),
            }
        )
        return PortfolioState(
            account_id=account_id,
            equity=Decimal(str(equity)),
            cash=Decimal(str(cash)),
            open_positions=len(self._positions),
            daily_realized_pnl=self._daily_realized_pnl,
            open_risk_dollars=self._daily_committed_loss,
            portfolio_gamma=sum((p.gamma for p in self._positions), Decimal("0")),
            portfolio_delta=sum((p.delta for p in self._positions), Decimal("0")),
            family_counts=tuple(sorted(family_counts.items())),
            expiration_counts=tuple(sorted(expiration_counts.items())),
            open_geometry_hashes=tuple(
                p.geometry_hash for p in self._positions if p.geometry_hash
            ),
            open_candidate_ids=tuple(
                p.candidate_id for p in self._positions if p.candidate_id
            ),
            state_id=state_id,
        )

    def _maybe_reset(self, session_date: str) -> None:
        if session_date != self._session:
            self._session = session_date
            self._positions.clear()
            self._daily_committed_loss = Decimal("0")
            self._daily_realized_pnl = Decimal("0")


def build_portfolio_state(
    *,
    account_id: str,
    equity: Decimal,
    cash: Decimal,
    open_positions: int = 0,
    daily_realized_pnl: Decimal = Decimal("0"),
    open_risk_dollars: Decimal = Decimal("0"),
    portfolio_gamma: Decimal = Decimal("0"),
    portfolio_delta: Decimal = Decimal("0"),
    family_counts: tuple[tuple[str, int], ...] = (),
    expiration_counts: tuple[tuple[str, int], ...] = (),
    open_geometry_hashes: tuple[str, ...] = (),
    open_candidate_ids: tuple[str, ...] = (),
) -> PortfolioState:
    return PortfolioState(
        account_id=account_id,
        equity=equity,
        cash=cash,
        open_positions=open_positions,
        daily_realized_pnl=daily_realized_pnl,
        open_risk_dollars=open_risk_dollars,
        portfolio_gamma=portfolio_gamma,
        portfolio_delta=portfolio_delta,
        family_counts=family_counts,
        expiration_counts=expiration_counts,
        open_geometry_hashes=open_geometry_hashes,
        open_candidate_ids=open_candidate_ids,
        state_id=content_hash(
            {
                "account_id": account_id,
                "equity": str(equity),
                "open_positions": open_positions,
                "open_risk": str(open_risk_dollars),
            }
        ),
    )
