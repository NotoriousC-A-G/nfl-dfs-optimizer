"""Unit-vs-unit matchup adjustment layer (docs/PRD.md Section 6: MatchupContext).

Every adjustment here traces back to a specific PFF team-level grade pair, never
a narrative judgment with no numeric input behind it -- if a team is missing
from the relevant facet map, the adjustment resolves to "no adjustment" (a
neutral 1.0 multiplier / None unit grades) rather than guessing.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

_MULTIPLIER_FLOOR = 0.85
_MULTIPLIER_CEIL = 1.15
_MULTIPLIER_SPAN_PER_Z = 0.10

_SKILL_POSITIONS = ("QB", "RB", "WR", "TE")


@dataclass(frozen=True)
class MatchupFacetInputs:
    """The six PFF team-grade facets MatchupContext is built from, this week.

    Each mapping is team abbreviation -> PFF grade (0-100 scale) for that facet.
    These are the same facets build_matchup_context_pool consumes, and the ones
    resolve_own_opponent_unit_grades picks a position-relevant pair from.
    """

    offense_run_blocking: Dict[str, float]
    defense_run: Dict[str, float]
    offense_pass_blocking: Dict[str, float]
    defense_pass_rush: Dict[str, float]
    defense_coverage_scheme: Dict[str, float]
    receiving_scheme: Dict[str, float]


@dataclass(frozen=True)
class OwnOpponentUnitGrades:
    """Own-team vs. opponent-team PFF grade pair relevant to one player's position."""

    own_unit_grade: Optional[float]
    opponent_unit_grade: Optional[float]


@dataclass(frozen=True)
class MatchupContext:
    """Unit-vs-unit adjustment for one player's matchup this week."""

    player_id: str
    position: str
    own_team: str
    opponent_team: str
    own_unit_grade: Optional[float]
    opponent_unit_grade: Optional[float]
    multiplier: float


def resolve_own_opponent_unit_grades(
    position: str,
    own_team: str,
    opponent_team: str,
    facets: MatchupFacetInputs,
) -> OwnOpponentUnitGrades:
    """Pick the position-relevant own-unit / opponent-unit PFF grade pair.

    - RB: own offensive line run-block grade vs. opponent's run-defense grade.
    - QB: own offensive line pass-block grade vs. opponent's pass-rush grade.
    - WR/TE: own team's receiving-scheme grade vs. opponent's coverage-scheme
      grade (an opponent-defense aggregate, not the specific alignment split
      PRD Section 6 describes for the full coverage adjustment -- that finer
      split isn't available at this layer yet).
    - Any other position (DST, etc.): no unit-grade pair is defined.

    A team missing from the relevant facet map resolves to None rather than
    raising, so a partial weekly PFF pull degrades to "no adjustment" instead of
    breaking the whole record.
    """
    if position == "RB":
        own = facets.offense_run_blocking.get(own_team)
        opponent = facets.defense_run.get(opponent_team)
    elif position == "QB":
        own = facets.offense_pass_blocking.get(own_team)
        opponent = facets.defense_pass_rush.get(opponent_team)
    elif position in ("WR", "TE"):
        own = facets.receiving_scheme.get(own_team)
        opponent = facets.defense_coverage_scheme.get(opponent_team)
    else:
        own = None
        opponent = None
    return OwnOpponentUnitGrades(own_unit_grade=own, opponent_unit_grade=opponent)


def _z_score(value: float, population: List[float]) -> float:
    if len(population) < 2:
        return 0.0
    stdev = statistics.pstdev(population)
    if stdev == 0:
        return 0.0
    return (value - statistics.mean(population)) / stdev


def _facet_populations(position: str, facets: MatchupFacetInputs) -> Tuple[List[float], List[float]]:
    if position == "RB":
        return list(facets.offense_run_blocking.values()), list(facets.defense_run.values())
    if position == "QB":
        return list(facets.offense_pass_blocking.values()), list(facets.defense_pass_rush.values())
    if position in ("WR", "TE"):
        return list(facets.receiving_scheme.values()), list(facets.defense_coverage_scheme.values())
    return [], []


def build_matchup_context_pool(
    players: Iterable[Tuple[str, str, str, str]],
    facets: MatchupFacetInputs,
) -> Dict[str, MatchupContext]:
    """Build a MatchupContext per player for a weekly slate.

    `players` is an iterable of (player_id, position, own_team, opponent_team).

    Grade-gap z-score differential (own-team grade z-score minus opponent-team
    grade z-score, each z-scored against that facet's slate-wide population)
    maps linearly to a multiplier capped to 0.85x-1.15x (PRD Section 6's
    proposed range). Positions without a defined unit-grade pair, or players
    whose team is missing from the relevant facet map, get a neutral 1.0
    multiplier instead of a guessed adjustment.
    """
    pool: Dict[str, MatchupContext] = {}
    for player_id, position, own_team, opponent_team in players:
        grades = resolve_own_opponent_unit_grades(position, own_team, opponent_team, facets)
        own_population, opponent_population = _facet_populations(position, facets)

        if (
            grades.own_unit_grade is None
            or grades.opponent_unit_grade is None
            or not own_population
            or not opponent_population
        ):
            multiplier = 1.0
        else:
            own_z = _z_score(grades.own_unit_grade, own_population)
            opponent_z = _z_score(grades.opponent_unit_grade, opponent_population)
            raw_multiplier = 1.0 + (own_z - opponent_z) * _MULTIPLIER_SPAN_PER_Z
            multiplier = max(_MULTIPLIER_FLOOR, min(_MULTIPLIER_CEIL, raw_multiplier))

        pool[player_id] = MatchupContext(
            player_id=player_id,
            position=position,
            own_team=own_team,
            opponent_team=opponent_team,
            own_unit_grade=grades.own_unit_grade,
            opponent_unit_grade=grades.opponent_unit_grade,
            multiplier=multiplier,
        )
    return pool
