"""Per-player real signal inputs `scoring.compute_agent_objective_delta` reads (NflAgentConstructor
Phase B3) -- a thin, read-only join over data the live pipeline already computes BEFORE lineup
generation (Phase B2's reordering moved `StackProfile`/circumstance/Component-A ceiling signals
earlier specifically so this bundle could exist). Reuses `composition/player_detail.py`'s own
private per-signal join helpers (`_ceiling_multiplier`, `_ownership_leverage`, `_stack_context`)
rather than re-deriving the joins -- same "reuse before inventing" (ADR-0011) discipline this
project applies everywhere else; see each of those functions' own docstrings for the exact join
keys this module relies on without repeating here.

Deliberately NOT `PlayerDetailRecord` itself: that type carries far more (injury, slate window,
receiving profile, etc.) than any agent axis below reads, and building it requires several inputs
(injury reports, kickoff times) that have nothing to do with lineup-construction preference and
would be pure overhead to thread through just for this.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nfl_dfs.ceiling.signals import CeilingSignal
from nfl_dfs.composition.player_detail import (
    StackContext,
    _ceiling_multiplier,
    _ownership_leverage,
    _stack_context,
)
from nfl_dfs.correlation.stack_profile import StackProfile
from nfl_dfs.matchup.context import MatchupContextResult
from nfl_dfs.normalization.identity import PlayerIdentity
from nfl_dfs.ownership.leverage import LeverageAssessment


@dataclass(frozen=True)
class PlayerSignals:
    """One player's real, already-computed signals -- every field is `None` when that signal
    genuinely doesn't apply or wasn't computed this week (never fabricated), matching
    `PlayerDetailRecord`'s own nullability discipline. `scoring.py` treats a `None` field as "this
    axis contributes zero for this player," never as a zero-valued real signal.
    """

    ceiling_multiplier: float | None
    leverage: LeverageAssessment | None
    matchup: MatchupContextResult | None
    stack_context: StackContext | None


@dataclass(frozen=True)
class SignalBundle:
    """Every reconciled player's `PlayerSignals`, keyed by `canonical_id`, plus the game-level
    implied totals `edge_condition` gating needs (`game_total`). Built once per live pull by
    `build_signal_bundle` and reused by every agent's `compute_agent_objective_delta` call --
    never recomputed per-agent.
    """

    signals_by_canonical_id: dict[str, PlayerSignals]
    implied_total_by_team: dict[str, float] = field(default_factory=dict)

    def game_total(self, home_team: str, away_team: str) -> float | None:
        """Sum of both teams' real implied totals for their shared game, or `None` if either is
        missing this pull -- never a guessed/partial total.
        """
        home = self.implied_total_by_team.get(home_team)
        away = self.implied_total_by_team.get(away_team)
        if home is None or away is None:
            return None
        return home + away


def build_signal_bundle(
    identities: list[PlayerIdentity],
    *,
    ceiling_signals_by_gsis_id: dict[str, CeilingSignal] | None = None,
    leverage_by_native_id: dict[str, LeverageAssessment] | None = None,
    matchup_context_by_canonical_id: dict[str, MatchupContextResult] | None = None,
    stack_profiles: list[StackProfile] | None = None,
    implied_total_by_team: dict[str, float] | None = None,
) -> SignalBundle:
    """Joins every real, already-computed signal onto each reconciled identity, keyed by
    `canonical_id` -- the same join keys `composition/player_detail.py`'s own
    `build_player_detail_record` already uses for these exact signals (reused directly here, not
    re-derived). Every input is optional and defaults to "nothing this week," same posture as
    `build_player_detail_record`'s own optional-caller-inputs.
    """
    signals: dict[str, PlayerSignals] = {}
    for identity in identities:
        gsis_id = identity.nflverse_gsis_id
        ceiling_multiplier, _ = _ceiling_multiplier(gsis_id, identity.position, ceiling_signals_by_gsis_id)
        leverage, _ = _ownership_leverage(identity, leverage_by_native_id)
        stack_context, _ = _stack_context(identity.team, gsis_id, stack_profiles)
        matchup = (matchup_context_by_canonical_id or {}).get(identity.canonical_id)
        signals[identity.canonical_id] = PlayerSignals(
            ceiling_multiplier=ceiling_multiplier,
            leverage=leverage,
            matchup=matchup,
            stack_context=stack_context,
        )
    return SignalBundle(
        signals_by_canonical_id=signals,
        implied_total_by_team=dict(implied_total_by_team or {}),
    )
