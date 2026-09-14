"""Stage 4: `MatchupContext` unit-vs-unit adjustment layer (PRD Section 6).

Implements the four `MatchupContext` rows against the already-ingested PFF grade facets
(`ingestion/pff.py`'s `GRADE_FACETS`, ADR-0014/ADR-0022):

- **Run game** (`run_game.py`) -- run-block vs. run-defense z-score differential, applied to a
  RB's efficiency.
- **Pass protection** (`pass_protection.py`) -- pass-block vs. pass-rush z-score differential,
  computed once per offense team and flowing downstream onto every pass-catcher on that team.
- **Coverage** (`coverage.py`) -- man/zone scheme differential (receiver's own `receiving/scheme`
  performance vs. the defense's `coverage_scheme` grade), weighted by ADR-0001's alignment
  (slot/perimeter) approximation and gated by ADR-0006's confidence checks; falls back to a
  team-wide snap-weighted grade when the gates fail or alignment data isn't available (see
  `coverage.py`'s module docstring for the real, current-state reason: the alignment facet itself
  is not yet ingested anywhere in this pipeline).
- **Scheme / game flow** -- produces no multiplier of its own; already wired into
  `GameEnvironmentScore`'s pace/PROE components via `ingestion/nflverse.py`. See
  `context.SCHEME_GAME_FLOW_NOTE`.

`combination.py` provides the shared capped log-space combination (ADR-0005) used when pass-
protection's flow-through multiplier and a receiver's own coverage multiplier both apply to the
same pass-catcher. `grading.py` provides the shared z-score-differential-to-multiplier mapping and
team-grade aggregation used by all three unit-vs-unit rows. `context.py` assembles all of this into
one `MatchupContextResult` per player (`build_matchup_context_pool` for a full slate) and is the
module `projection/blend.py` and `composition/player_detail.py` both consume.
"""

from nfl_dfs.matchup.context import (
    MatchupContextResult,
    MatchupFacetInputs,
    MatchupRowResult,
    PlayerMatchupInput,
    build_matchup_context_for_qb,
    build_matchup_context_for_rb,
    build_matchup_context_for_receiver,
    build_matchup_context_pool,
    neutral_matchup_context,
    resolve_own_opponent_unit_grades,
)

__all__ = [
    "MatchupContextResult",
    "MatchupFacetInputs",
    "MatchupRowResult",
    "PlayerMatchupInput",
    "build_matchup_context_for_qb",
    "build_matchup_context_for_rb",
    "build_matchup_context_for_receiver",
    "build_matchup_context_pool",
    "neutral_matchup_context",
    "resolve_own_opponent_unit_grades",
]
