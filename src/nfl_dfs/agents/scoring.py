"""`compute_agent_objective_delta`: turns one `NflAgentConstructor`'s parameter vector into the
`objective_delta_by_id` dict `optimizer/lineup.py`'s solver hook (PR B1) accepts (NflAgentConstructor
Phase B3).

**Delta-dispatch discipline (mirrors the sister MLB project's own resolved design, see
`constructor.py`'s module docstring):** this module only ever PRODUCES a separate delta dict --
it never writes into `PlayerProjection.blended_projection` itself. That indirection is deliberate:
the MLB project's own history includes a real "silent no-op" incident where a direct-write path
left 6 preference fields dead for days, undetected, because nothing verified the write actually
reached the objective. Routing every agent through this one function and `lineup.py`'s one
`objective_delta_by_id` param makes that class of bug structurally harder to reintroduce.

**Magnitude discipline:** every axis's raw, per-player contribution is first computed as a
dimensionless "centered signal" roughly in -1..+1 (see each axis's own comment below for its
centering/normalization), summed across whichever axes apply to that player, then scaled by
`_MAX_IQR_FRACTION_PER_AXIS` of the POOL'S OWN real `blended_projection` interquartile range --
never a fixed point constant. This mirrors the MLB project's own fix for its second real incident
(a "nudge-magnitude" bug where fixed constants were noise against real point totals): a nudge
that's meaningful against a $3-15 IQR slate should not also apply full-strength to a $20-40 IQR
slate.

**A disclosed, draft magnitude, not a backtested one (ADR-0019/0020's own "draft, not backtested"
convention):** `_MAX_IQR_FRACTION_PER_AXIS` and the edge-condition thresholds below are reasonable
starting points, not validated against real outcomes. Phase C's retroactive backtesting against the
real 2020-2025 ResultsDB is the intended path to tuning or replacing them -- not attempted here.
"""

from __future__ import annotations

import statistics

from nfl_dfs.agents.constructor import EdgeCondition, NflAgentConstructor
from nfl_dfs.agents.signal_bundle import PlayerSignals, SignalBundle
from nfl_dfs.composition.player_detail import StackContext
from nfl_dfs.projection.blend import PlayerProjection

# Disclosed, draft magnitude (see module docstring) -- at slider=+-1.0 with every relevant signal
# at its most extreme real value, one axis's contribution tops out at this fraction of the pool's
# own blended_projection IQR. Axes are NOT jointly capped when more than one applies to the same
# player (e.g. a stack candidate in a favorable matchup can get both a matchup_conviction AND a
# stack_conviction contribution) -- a deliberate simplification for this first pass, not a bug.
_MAX_IQR_FRACTION_PER_AXIS = 0.5

# GameScriptLean.intensity's real range (spread_dampener's own bands, correlation/stack_profile.py)
# is [0.25, 1.00] -- centered here so a close game (near 1.00) reads as a positive "close-game"
# signal and a blowout (near 0.25) reads as negative.
_INTENSITY_MIDPOINT = 0.625
_INTENSITY_HALF_RANGE = 0.375

# game_stack_viability is a 0-100 composite (correlation/stack_profile.py), centered at its
# midpoint the same way.
_VIABILITY_MIDPOINT = 50.0
_VIABILITY_HALF_RANGE = 50.0

# Disclosed, draft edge-condition thresholds -- not backtested (see module docstring).
CLOSE_SPREAD_THRESHOLD_POINTS = 3.0
HIGH_TOTAL_THRESHOLD_POINTS = 47.0


def _pool_iqr(pool: list[PlayerProjection]) -> float:
    """The pool's own real `blended_projection` interquartile range -- 0.0 (never fabricated) if
    fewer than 4 players have a usable projection, the floor below which quartiles stop being a
    meaningful spread measure.
    """
    values = sorted(p.blended_projection for p in pool if p.blended_projection is not None)
    if len(values) < 4:
        return 0.0
    q1, _, q3 = statistics.quantiles(values, n=4)
    return q3 - q1


def _game_matches_edge_condition(
    edge_condition: EdgeCondition | None, stack_context: StackContext, signal_bundle: SignalBundle
) -> bool:
    """`None` always matches (no game-level gate). `"high_total"` fails closed -- never matches --
    when this pull has no real implied total for either side of the game, same "never fabricate"
    posture as every nullable field in this project.
    """
    if edge_condition is None:
        return True
    is_close = abs(stack_context.home_spread) <= CLOSE_SPREAD_THRESHOLD_POINTS
    total = signal_bundle.game_total(stack_context.home_team, stack_context.away_team)
    is_high_total = total is not None and total >= HIGH_TOTAL_THRESHOLD_POINTS
    if edge_condition == "close_spread":
        return is_close
    if edge_condition == "high_total":
        return is_high_total
    if edge_condition == "close_spread_or_high_total":
        return is_close or is_high_total
    raise AssertionError(f"unreachable -- unknown edge_condition {edge_condition!r}")  # pragma: no cover


def _stack_terms(agent: NflAgentConstructor, signals: PlayerSignals, signal_bundle: SignalBundle) -> float:
    """`game_script_lean_weight`/`stack_conviction`'s combined contribution -- 0.0 for a player who
    isn't a real stack candidate at all, or whose only candidacy is a bring-back this agent's
    `bring_back_allowed`/`bring_back_rb_allowed` doesn't permit, or whose game doesn't match this
    agent's `edge_condition`.
    """
    stack_context = signals.stack_context
    if stack_context is None:
        return 0.0

    is_eligible_pass_catcher = stack_context.is_primary_stack_candidate or (
        agent.bring_back_allowed and stack_context.is_bring_back_candidate
    )
    is_eligible_rb = stack_context.is_primary_rb_stack_candidate or (
        agent.bring_back_rb_allowed and stack_context.is_bring_back_rb_candidate
    )
    if not is_eligible_pass_catcher and not is_eligible_rb:
        return 0.0
    if not _game_matches_edge_condition(agent.edge_condition, stack_context, signal_bundle):
        return 0.0

    total = 0.0
    if agent.game_script_lean_weight != 0.0 and stack_context.game_script_lean is not None:
        centered_intensity = (
            stack_context.game_script_lean.intensity - _INTENSITY_MIDPOINT
        ) / _INTENSITY_HALF_RANGE
        total += agent.game_script_lean_weight * centered_intensity
    if agent.stack_conviction != 0.0 and stack_context.game_stack_viability is not None:
        centered_viability = (stack_context.game_stack_viability - _VIABILITY_MIDPOINT) / _VIABILITY_HALF_RANGE
        total += agent.stack_conviction * centered_viability
    return total


def _raw_contribution(agent: NflAgentConstructor, signals: PlayerSignals, signal_bundle: SignalBundle) -> float:
    """Sum of every axis's centered, roughly -1..+1-scaled contribution for one player. A `None`
    signal contributes exactly 0.0 for that axis -- never guessed.
    """
    total = 0.0
    if agent.ceiling_lean != 0.0 and signals.ceiling_multiplier is not None:
        total += agent.ceiling_lean * (signals.ceiling_multiplier - 1.0)
    if agent.ownership_stance != 0.0 and signals.leverage is not None:
        # LeverageAssessment.ownership_percentile is 0.0 = MOST owned, 1.0 = LEAST owned
        # (ownership/leverage.py's own `_ownership_percentiles` docstring) -- inverted from the
        # intuitive reading, so ownership_stance's sign is flipped here: a negative (contrarian)
        # stance must BOOST a high percentile (least-owned) player, not a low one.
        total += -agent.ownership_stance * (signals.leverage.ownership_percentile - 0.5)
    if agent.matchup_conviction != 0.0 and signals.matchup is not None:
        total += agent.matchup_conviction * (signals.matchup.combined_multiplier - 1.0)
    total += _stack_terms(agent, signals, signal_bundle)
    return total


def compute_agent_objective_delta(
    agent: NflAgentConstructor,
    pool: list[PlayerProjection],
    signal_bundle: SignalBundle,
) -> dict[str, float]:
    """The real, per-player delta dict for `optimizer.lineup.generate_lineups`'
    `objective_delta_by_id` -- every entry scaled to this real pool's own `blended_projection` IQR
    (never a fixed constant, see module docstring). Returns `{}` (a fully inert agent this run,
    never a fabricated nudge) when the pool has too few players for a meaningful IQR.

    A player with no applicable signal for any of `agent`'s nonzero axes is simply absent from the
    returned dict (equivalent to a 0.0 delta via `objective_delta_by_id.get(..., 0.0)`), matching
    this project's "no entry, no effect" convention already established by
    `game_environment_scores`.
    """
    iqr = _pool_iqr(pool)
    if iqr <= 0.0:
        return {}

    deltas: dict[str, float] = {}
    for p in pool:
        signals = signal_bundle.signals_by_canonical_id.get(p.canonical_id)
        if signals is None:
            continue
        raw = _raw_contribution(agent, signals, signal_bundle)
        if raw == 0.0:
            continue
        deltas[p.canonical_id] = raw * _MAX_IQR_FRACTION_PER_AXIS * iqr
    return deltas
