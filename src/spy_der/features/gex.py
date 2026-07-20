"""GEX computation and adaptive rank state (master spec §18).

Migrated from System A ``gex/base.py`` (OI signed-dollar-gamma aggregation,
gamma flip by cumulative-gamma sign crossing, call/put walls, concentration)
and ``gex_window.py`` (disk-persisted rolling |net GEX| percentile that reads a
neutral 0.5 until warm and survives restarts). Behavior preserved (0DTE @
de4a6e7); the arithmetic and sign conventions match the source.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal

from spy_der.contracts.market import CanonicalMarketSnapshot, OptionType
from spy_der.contracts.structure import GexLevels

__all__ = ["GexRankWindow", "compute_oi_gex"]

_MULT = 100  # index/ETF options multiplier (matches System A gex/base.MULT)


@dataclass(frozen=True, slots=True)
class _Weighted:
    side: str
    strike: float
    gamma: float
    weight: float


def _signed_dollar_gamma(side: str, gamma: float, weight: float, spot: float) -> float:
    # Baseline dealer-short-calls / long-puts: calls +, puts -.
    dollar = gamma * weight * _MULT * spot * spot * 0.01
    return dollar if side == "call" else -dollar


def _weighted_contracts(snapshot: CanonicalMarketSnapshot) -> list[_Weighted]:
    out: list[_Weighted] = []
    for quote in snapshot.option_chain:
        gamma = quote.gamma
        weight = quote.open_interest
        if gamma is None or weight is None or weight <= 0 or not math.isfinite(gamma):
            continue
        side = "call" if quote.contract.option_type is OptionType.CALL else "put"
        out.append(_Weighted(side, float(quote.contract.strike), gamma, float(weight)))
    return out


def compute_oi_gex(snapshot: CanonicalMarketSnapshot) -> GexLevels | None:
    """OI-weighted GEX levels for ``snapshot``; ``None`` if no usable contracts."""
    spot = float(snapshot.underlying_price)
    contracts = _weighted_contracts(snapshot)
    if not contracts:
        return None

    by_strike: dict[float, float] = {}
    call_g: dict[float, float] = {}
    put_g: dict[float, float] = {}
    for c in contracts:
        signed = _signed_dollar_gamma(c.side, c.gamma, c.weight, spot)
        by_strike[c.strike] = by_strike.get(c.strike, 0.0) + signed
        target = call_g if c.side == "call" else put_g
        target[c.strike] = target.get(c.strike, 0.0) + abs(signed)

    net_gex = sum(by_strike.values()) / 1e9
    gross_gex = sum(abs(v) for v in by_strike.values()) / 1e9
    net_ratio = (net_gex / gross_gex) if gross_gex else 0.0

    flip = _gamma_flip(by_strike, spot)
    call_wall = _call_wall(call_g, spot)
    put_wall = _put_wall(put_g, spot)

    if gross_gex > 0:
        concentration = max(abs(v) for v in by_strike.values()) / 1e9 / gross_gex
    else:
        concentration = 0.0
    side_call = sum(call_g.values()) or 1.0
    side_put = sum(put_g.values()) or 1.0
    wall_c = (call_g.get(call_wall, 0.0) / side_call) if call_g else 0.0
    wall_p = (put_g.get(put_wall, 0.0) / side_put) if put_g else 0.0
    wall_concentration = max(wall_c, wall_p)

    return GexLevels(
        net_gex_bn=net_gex,
        net_ratio=net_ratio,
        gamma_flip=_to_decimal(flip),
        call_wall=_to_decimal(call_wall),
        put_wall=_to_decimal(put_wall),
        gex_concentration=concentration,
        wall_concentration=wall_concentration,
        n_contracts=len(contracts),
        n_strikes=len(by_strike),
    )


def _gamma_flip(by_strike: dict[float, float], spot: float) -> float:
    strikes = sorted(by_strike)
    cum = 0.0
    prev_k, prev_cum = strikes[0], 0.0
    for k in strikes:
        cum += by_strike[k]
        if prev_cum < 0 <= cum or prev_cum > 0 >= cum:
            span = cum - prev_cum
            return prev_k + (k - prev_k) * (0 - prev_cum) / span if span else k
        prev_k, prev_cum = k, cum
    return spot


def _call_wall(call_g: dict[float, float], spot: float) -> float:
    above = {k: g for k, g in call_g.items() if k >= spot}
    if above:
        return max(above, key=lambda k: above[k])
    if call_g:
        return max(call_g, key=lambda k: call_g[k])
    return spot


def _put_wall(put_g: dict[float, float], spot: float) -> float:
    below = {k: g for k, g in put_g.items() if k <= spot}
    if below:
        return max(below, key=lambda k: below[k])
    if put_g:
        return max(put_g, key=lambda k: put_g[k])
    return spot


def _to_decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


@dataclass
class GexRankWindow:
    """Disk-persisted rolling |net GEX| percentile (spec §18 persistent state).

    Migrated from System A ``gex_window.GexRankWindow``: ranks abs(net_gex)
    against a multi-day history, reads a neutral 0.5 until ``min_samples``, and
    survives restarts via atomic JSON. ``path=None`` keeps it memory-only.
    """

    path: str | None = None
    max_age_days: float = 10.0
    max_entries: int = 5000
    min_samples: int = 30
    _entries: list[tuple[float, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._load()

    @property
    def is_warm(self) -> bool:
        return len(self._entries) >= self.min_samples

    def __len__(self) -> int:
        return len(self._entries)

    def rank(self, net_gex: float, now_epoch: float) -> float:
        """Record this print and return the percentile rank of ``|net_gex|``."""
        self._load()
        mag = abs(float(net_gex))
        self._entries.append((float(now_epoch), mag))
        self._prune(float(now_epoch))
        self._save()
        if len(self._entries) < self.min_samples:
            return 0.5
        below = sum(1 for _, m in self._entries if m < mag)
        return below / len(self._entries)

    def _prune(self, now_epoch: float) -> None:
        cutoff = now_epoch - self.max_age_days * 86400.0
        self._entries = [(t, m) for t, m in self._entries if t >= cutoff]
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries :]

    def _load(self) -> None:
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
            self._entries = [(float(t), float(m)) for t, m in data.get("entries", [])]
        except (OSError, ValueError, TypeError):
            self._entries = []  # corrupt state re-warms; never crash the feed

    def _save(self) -> None:
        if not self.path:
            return
        try:
            directory = os.path.dirname(self.path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".gex_", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"entries": self._entries}, handle)
            os.replace(tmp, self.path)
        except OSError:
            pass  # persistence is best-effort; never break a tick
