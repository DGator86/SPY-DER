"""MarketExperienceProvider over SPY-DER's own canonical recordings.

The Dojo was built when 0DTE was the only thing that had ever recorded a
market, so its one window onto experience was a directory of ``MarketPacket``
JSON — the cross-repository handoff format. Nothing inside SPY-DER writes that
format. The consequence was quiet and total: with recordings sitting under
``<state-root>/market`` from both the 0DTE import and the live market service,
every Dojo phase still reported ``no MarketExperienceProvider`` and did
nothing. The learning loop was the last subsystem still pointed at the old
repository for its data.

This module points it at SPY-DER's own tape. One recorded snapshot becomes one
:class:`MarketPacket`, built through the same production path the engine runs:

    snapshot → generate_candidate_universe → calculate_universe_economics

so the candidate surface the Dojo scores is the candidate surface that ships,
priced by the same economics. Nothing is copied from a derived artifact under
``candidates/`` — as in :mod:`spy_der.training.observations`, the tape must be
reproducible from the market recording alone, or a replay silently depends on
whichever engine version happened to write the artifact.

Outcomes are what a position would actually have *realized*, not what it was
worth at expiry: :func:`~spy_der.evaluation.managed_outcome.simulate_managed_exit`
walks the bar path and closes the structure where the production exit policy
says to. SPY-DER never holds to settlement, so scoring terminal payoff would
credit trades with money the account never saw. That gives a per-candidate P&L
map, which is what lets the Dojo measure *selection regret* — not just whether
a trade won, but how it compared with the best trade available at that tick.

The policy must match the one the candidate-value model was trained under, or
the Dojo and the model disagree about what a good trade is.

Three rules keep the tape honest:

* **Outcomes are served only through** :meth:`NativeTapeProvider.outcome`.
  ``MarketPacket.forecast`` is a documented carrier for embedded labels, and
  the temptation is to put them there so packets are self-contained. Realized
  P&L in a field named ``forecast`` is a lookahead landmine one future
  authority away from reading it, so the label path stays out of the packet.
* **An unfinished session settles nothing.** A position that never reached a
  close has no settlement price to fall back on when no exit fired — a session
  whose bars stop at noon offers a midday quote. Such a session yields packets
  and no outcomes rather than a number that reads like a result.
* **Sampling is by wall-clock interval, not by record index.** Recording
  cadence differs between the 0DTE import and SPY-DER's own service, and
  between configurations of each. Striding every Nth record would make the tape
  size depend on the recorder; striding every N minutes does not.

Two limitations are real, deliberate, and reported by :meth:`
NativeTapeProvider.warnings` rather than left to be discovered:

* **No forecast.** ``forecast_uncertainty`` stays ``0.0`` with ``forecast``
  empty, so the uncertainty-gated knobs
  (:meth:`~spy_der.decisions.knobs.DecisionKnobs.effective_hard_vetoes`) are
  inert. A manufactured uncertainty would score that gate against a number
  nobody measured.
* **No candidate utility, therefore no measured selection.**
  :func:`~spy_der.economics.service.calculate_candidate_economics` only
  produces an ``expected_value`` when its caller supplies ``expected_net_pnl``,
  and that comes from the candidate-value model. Fit one with
  ``spy-der-train`` and pass it as ``value_model``; without it,
  :class:`~spy_der.agents.deterministic.DeterministicDecisionAgent` sorts on a
  ``None`` utility and falls through to candidate id. The Dojo still measures
  knob effects honestly, but *which* candidate gets picked is alphabetical.
  ``v3_rank`` is left ``None`` in that state so nothing downstream mistakes the
  ordering for a ranking.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from spy_der.candidates.factory import generate_candidate_universe
from spy_der.contracts.candidates import Candidate
from spy_der.contracts.economics import CandidateEconomics
from spy_der.contracts.integration import (
    MARKET_PACKET_SCHEMA,
    OUTCOME_PACKET_SCHEMA,
    MarketCandidateView,
    MarketPacket,
    OutcomePacket,
)
from spy_der.contracts.market import Bar, CanonicalMarketSnapshot
from spy_der.contracts.positions import ExitPolicy
from spy_der.economics.service import calculate_universe_economics
from spy_der.evaluation.managed_outcome import simulate_managed_exit
from spy_der.evaluation.settlement import (
    session_bar_path,
    session_settlement_price,
)
from spy_der.market_data.replay import CorruptRecordingError
from spy_der.training.observations import load_session_snapshots

__all__ = [
    "DEFAULT_INTERVAL_MINUTES",
    "DEFAULT_NEUTRAL_BAND",
    "NativeTapeProvider",
    "load_value_model",
]

log = logging.getLogger("spy_der.dojo.native_tape")

#: Minutes of wall-clock between sampled packets. Candidate generation plus
#: economics costs a few hundred milliseconds per snapshot, so a full 1-minute
#: tape over eight sessions is tens of minutes of Dojo startup. Five minutes
#: keeps a session near 78 packets — comfortably over ``DojoConfig.min_ticks``
#: at the three-session minimum — for a few seconds per session.
DEFAULT_INTERVAL_MINUTES = 5

#: Fractional move below which a session's realized direction is called
#: ``neutral`` rather than bullish/bearish. Candidate directions include a
#: neutral class, so a pure sign test could never score one correctly. The band
#: is a stated assumption, not a tuned constant — it exists to make the
#: direction-hit metric meaningful and is overridable per provider.
DEFAULT_NEUTRAL_BAND = 0.001


def _session_from_stem(stem: str) -> date | None:
    try:
        return date.fromisoformat(stem)
    except ValueError:
        return None


def _realized_direction(
    entry: Decimal, settle: Decimal, *, neutral_band: float
) -> str:
    if entry <= 0:
        return "neutral"
    move = float((settle - entry) / entry)
    if abs(move) < neutral_band:
        return "neutral"
    return "bullish" if move > 0 else "bearish"


def _quality_score(snapshot: CanonicalMarketSnapshot) -> float:
    """Map the snapshot's accumulated quality penalty onto ``[0, 1]``."""
    return min(max(1.0 - float(snapshot.data_quality.penalty), 0.0), 1.0)


def load_value_model(
    state_root: str | Path,
    *,
    model_id: str | None = None,
    load_mode: str = "research",
) -> tuple[Any | None, str]:
    """Newest registered candidate-value model; ``(model, note)``.

    Returns ``(None, reason)`` rather than raising. A Dojo run without a value
    model is degraded, not broken — it still measures knob effects — and
    failing the whole run because no model has been fitted yet would make the
    common early state unrunnable.
    """
    from spy_der.training.registry import ModelRegistry, RegistryError

    directory = Path(state_root) / "models"
    if not directory.is_dir():
        return None, f"no model registry at {directory}"
    registry = ModelRegistry(str(directory))

    chosen = model_id
    if chosen is None:
        candidates: list[tuple[str, str]] = []
        for meta_path in sorted(directory.glob("candidate_value-*.json")):
            try:
                meta = registry.load_metadata(meta_path.stem, validate_v2=False)
            except (RegistryError, OSError, ValueError):
                continue
            candidates.append((str(meta.get("created_at") or ""), meta_path.stem))
        if not candidates:
            return None, "no candidate-value model registered"
        chosen = max(candidates)[1]

    try:
        model, meta = registry.load(chosen, load_mode=load_mode)
    except (RegistryError, OSError, ValueError) as exc:
        return None, f"could not load {chosen}: {exc}"
    return model, f"{chosen} (status {meta.get('status', 'unknown')})"


class NativeTapeProvider:
    """Recorded SPY-DER sessions as Dojo experience.

    Args:
        state_root: SPY-DER state directory; recordings are read from
            ``<state_root>/market/*.jsonl``.
        interval_minutes: minimum wall-clock spacing between sampled packets.
        neutral_band: see :data:`DEFAULT_NEUTRAL_BAND`.
        symbol: underlying to report on packets whose snapshot omits one.
        value_model: a fitted
            :class:`~spy_der.candidate_value.models.value.CandidateValueModel`.
            This is what turns the tape from measuring knob effects into
            measuring *selection* — without it every candidate carries
            ``utility=None`` and the deciding agent falls through to candidate
            id. Load one with
            :func:`~spy_der.dojo.native_tape.load_value_model`.
    """

    def __init__(
        self,
        state_root: str | Path,
        *,
        interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
        neutral_band: float = DEFAULT_NEUTRAL_BAND,
        symbol: str = "SPY",
        value_model: Any | None = None,
        exit_policy: ExitPolicy | None = None,
    ) -> None:
        self.market_dir = Path(state_root) / "market"
        self.interval_minutes = max(int(interval_minutes), 0)
        self.neutral_band = max(float(neutral_band), 0.0)
        self.symbol = symbol
        self.value_model = value_model
        # Outcomes are what a position would have realized under this policy,
        # matching what the candidate-value model is trained on. Scoring
        # settlement value while the model predicts managed value would have
        # the Dojo and the model disagree about what a good trade is.
        self.exit_policy = exit_policy or ExitPolicy()
        self._outcomes: dict[str, OutcomePacket] = {}
        self._loaded: set[date] = set()
        # Reported once by `warnings()` rather than logged per snapshot. The
        # unpriced tally is a set of snapshot ids, not a counter: the Dojo
        # re-walks the tape once per phase, and a counter would report three
        # times the ticks the tape actually holds.
        self._unsettled: list[date] = []
        self._unpriced: set[str] = set()
        self._priced_seen = False

    # ---------------------------------------------------------------- protocol

    def sessions(self) -> list[date]:
        if not self.market_dir.is_dir():
            return []
        found = {
            session
            for path in self.market_dir.glob("*.jsonl")
            if (session := _session_from_stem(path.stem)) is not None
        }
        return sorted(found)

    def snapshots(self, session: date) -> Iterable[MarketPacket]:
        packets, outcomes = self._build_session(session)
        self._outcomes.update(outcomes)
        self._loaded.add(session)
        return packets

    def outcome(self, snapshot_id: str) -> OutcomePacket | None:
        if snapshot_id in self._outcomes:
            return self._outcomes[snapshot_id]
        # Out-of-order access: the recorded phase always walks `snapshots()`
        # first, but the protocol does not promise it, and returning None for a
        # session we simply have not read yet would silently drop its outcomes.
        for session in self.sessions():
            if session in self._loaded:
                continue
            self.snapshots(session)
            if snapshot_id in self._outcomes:
                return self._outcomes[snapshot_id]
        return None

    # ----------------------------------------------------------------- reporting

    def warnings(self) -> list[str]:
        """Conditions that quietly weaken a run, worth saying out loud."""
        out: list[str] = []
        if self._unsettled:
            listed = ", ".join(s.isoformat() for s in sorted(self._unsettled))
            out.append(
                f"tape_unsettled: {len(self._unsettled)} session(s) never "
                f"reached the close, so they contribute market state but no "
                f"outcomes: {listed}"
            )
        if self._unpriced and not self._priced_seen:
            # This is the difference between the Dojo measuring judgement and
            # the Dojo measuring the alphabet. `calculate_candidate_economics`
            # only yields an expected value when a candidate-value forecast
            # supplies `expected_net_pnl`; with none, the deterministic agent
            # sorts on a None utility and falls through to candidate id. Knob
            # effects are still measured, but candidate selection is arbitrary.
            out.append(
                f"tape_unpriced: no candidate carried an expected value on any "
                f"of {len(self._unpriced)} tick(s) — selection is arbitrary and "
                f"only knob effects are being scored. Train a candidate-value "
                f"model to make the recorded phase measure selection."
            )
        return out

    # ------------------------------------------------------------------ internal

    def _session_path(self, session: date) -> Path:
        return self.market_dir / f"{session.isoformat()}.jsonl"

    def _sample(
        self, snapshots: Sequence[CanonicalMarketSnapshot]
    ) -> list[CanonicalMarketSnapshot]:
        if self.interval_minutes <= 0:
            return list(snapshots)
        spacing = self.interval_minutes * 60
        sampled: list[CanonicalMarketSnapshot] = []
        last_ts: float | None = None
        for snapshot in snapshots:
            stamp = snapshot.timestamp.timestamp()
            if last_ts is None or stamp - last_ts >= spacing:
                sampled.append(snapshot)
                last_ts = stamp
        return sampled

    def _build_session(
        self, session: date
    ) -> tuple[list[MarketPacket], dict[str, OutcomePacket]]:
        path = self._session_path(session)
        if not path.is_file():
            return [], {}
        try:
            snapshots, unparseable = load_session_snapshots(path)
        except CorruptRecordingError as exc:
            log.error("recording %s failed integrity checks: %s", session, exc)
            return [], {}
        except OSError as exc:
            log.error("recording %s is unreadable: %s", session, exc)
            return [], {}
        if unparseable:
            log.warning(
                "%d snapshot(s) in %s could not be rebuilt", unparseable, session
            )
        if not snapshots:
            return [], {}

        bar_path = session_bar_path(snapshots)
        settle = session_settlement_price(bar_path)
        if settle is None and session not in self._unsettled:
            self._unsettled.append(session)

        packets: list[MarketPacket] = []
        outcomes: dict[str, OutcomePacket] = {}
        for snapshot in self._sample(snapshots):
            universe = generate_candidate_universe(snapshot)
            if not universe.candidates:
                # No chain, or a chain too thin to build an approved family
                # from. A packet with no candidates can only ever produce an
                # abstention, which would dilute every rate the Dojo reports.
                continue
            economics = {
                e.candidate_id: e
                for e in calculate_universe_economics(universe, snapshot)
            }
            utilities = self._utilities(universe.candidates, economics)
            ranked, priced = self._rank(universe.candidates, economics, utilities)
            if priced:
                self._priced_seen = True
            else:
                self._unpriced.add(snapshot.snapshot_id)
            packet = self._packet(
                snapshot, session, ranked, economics, utilities, priced=priced
            )
            packets.append(packet)
            if settle is not None:
                built = self._outcome(snapshot, packet, ranked, bar_path, settle)
                if built is not None:
                    outcomes[packet.snapshot_id] = built
        return packets, outcomes

    def _utilities(
        self,
        candidates: Sequence[Candidate],
        economics: dict[str, CandidateEconomics],
    ) -> dict[str, float]:
        """Risk-adjusted utility per candidate, when a value model is attached.

        Empty without one, and that is the whole difference between the Dojo
        measuring judgement and the Dojo measuring the alphabet.
        """
        if self.value_model is None:
            return {}
        from spy_der.candidate_value.models.value import build_feature_row

        out: dict[str, float] = {}
        for candidate in candidates:
            econ = economics.get(candidate.candidate_id)
            if econ is None:
                continue
            try:
                forecast = self.value_model.predict_one(
                    build_feature_row(candidate, econ),
                    candidate=candidate,
                    economics=econ,
                )
            except (RuntimeError, ValueError) as exc:
                # One unscoreable candidate must not cost the tick. Absent
                # utility is already a state every consumer handles.
                log.warning("candidate value failed for %s: %s", candidate.candidate_id, exc)
                continue
            value = forecast.utility
            if value is None:
                value = forecast.expected_net_pnl
            if value is not None:
                out[candidate.candidate_id] = float(value)
        return out

    def _rank(
        self,
        candidates: Sequence[Candidate],
        economics: dict[str, CandidateEconomics],
        utilities: dict[str, float],
    ) -> tuple[list[Candidate], bool]:
        """Best value first; ``(ordered, priced)``.

        ``priced`` is False when nothing carried a value, which is the state of
        a tape with no candidate-value model attached:
        `calculate_candidate_economics` computes an ``expected_value`` only when
        its caller supplies ``expected_net_pnl``. The caller must not present
        the resulting order as a ranking — see :meth:`_packet`.
        """

        def value_of(candidate: Candidate) -> float | None:
            if candidate.candidate_id in utilities:
                return utilities[candidate.candidate_id]
            econ = economics.get(candidate.candidate_id)
            raw = econ.expected_value if econ is not None else None
            return float(raw) if raw is not None else None

        def key(candidate: Candidate) -> tuple[float, str]:
            value = value_of(candidate)
            return (
                -value if value is not None else float("inf"),
                candidate.candidate_id,
            )

        priced = any(value_of(c) is not None for c in candidates)
        return sorted(candidates, key=key), priced

    def _packet(
        self,
        snapshot: CanonicalMarketSnapshot,
        session: date,
        ranked: Sequence[Candidate],
        economics: dict[str, CandidateEconomics],
        utilities: dict[str, float],
        *,
        priced: bool,
    ) -> MarketPacket:
        views: list[MarketCandidateView] = []
        for rank, candidate in enumerate(ranked, start=1):
            econ = economics.get(candidate.candidate_id)
            utility = utilities.get(candidate.candidate_id)
            if utility is None and econ is not None and econ.expected_value is not None:
                utility = float(econ.expected_value)
            views.append(
                MarketCandidateView(
                    candidate_id=candidate.candidate_id,
                    family=candidate.family,
                    direction=candidate.direction,
                    maximum_loss=candidate.maximum_loss,
                    capital_required=candidate.capital_required,
                    geometry_hash=candidate.geometry_hash,
                    expiration=candidate.expiration,
                    mid_price=econ.mid_price if econ is not None else None,
                    fill_probability=(
                        float(econ.fill_probability) if econ is not None else 1.0
                    ),
                    utility=utility,
                    # Without an expected value the order above is alphabetical
                    # by candidate id, and a 1..N index would dress that up as
                    # a ranking for anything that reads `v3_rank` as a tiebreak.
                    v3_rank=rank if priced else None,
                )
            )
        return MarketPacket(
            schema_version=MARKET_PACKET_SCHEMA,
            snapshot_id=snapshot.snapshot_id,
            session_date=session,
            symbol=snapshot.underlying_symbol or self.symbol,
            underlying_price=snapshot.underlying_price,
            data_quality=_quality_score(snapshot),
            forecast_uncertainty=0.0,
            hard_vetoes=(),
            forecast={},
            candidates=tuple(views),
            generated_at=snapshot.timestamp,
        )

    def _outcome(
        self,
        snapshot: CanonicalMarketSnapshot,
        packet: MarketPacket,
        ranked: Sequence[Candidate],
        bar_path: Sequence[Bar],
        settle: Decimal,
    ) -> OutcomePacket | None:
        by_candidate: dict[str, str] = {}
        reference: Candidate | None = None
        for candidate in ranked:
            outcome = simulate_managed_exit(
                candidate,
                chain=snapshot.option_chain,
                bars=bar_path,
                observed_at=snapshot.timestamp,
                session=packet.session_date,
                settlement=settle,
                policy=self.exit_policy,
            )
            if outcome is None:
                continue
            pnl = outcome.realized_pnl
            by_candidate[candidate.candidate_id] = str(pnl)
            if reference is None:
                reference = candidate
        if not by_candidate or reference is None:
            return None
        # `realized_pnl` is the scalar the evaluator falls back on when a
        # decision names a candidate outside the map — impossible here, since
        # decisions are made from this same universe, but the field may not be
        # None or the match is dropped before the per-candidate map is ever
        # consulted. The best-expected-value candidate is the honest reference:
        # it is what this tick's economics actually recommended.
        return OutcomePacket(
            schema_version=OUTCOME_PACKET_SCHEMA,
            snapshot_id=packet.snapshot_id,
            session_date=packet.session_date,
            symbol=packet.symbol,
            candidate_id=reference.candidate_id,
            action="SETTLED",
            realized_pnl=Decimal(by_candidate[reference.candidate_id]),
            settled=True,
            labels={
                "true_direction": _realized_direction(
                    snapshot.underlying_price,
                    settle,
                    neutral_band=self.neutral_band,
                ),
                "realized_pnl_by_candidate": by_candidate,
            },
            settled_at=snapshot.timestamp,
        )
