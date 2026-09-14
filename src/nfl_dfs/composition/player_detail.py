"""Assembles a single player's Player Detail tab record (docs/PRD.md Section 8/10).

build_player_detail_record is the one place a PlayerDetailRecord gets built.
Callers that assemble the dashboard's player pool (e.g.
scripts/live_integration_check_dashboard.py) are responsible for fetching a
week's MatchupFacetInputs and passing it in -- without it, the matchup grade
columns fall back to None rather than failing the build.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from nfl_dfs.matchup.context import MatchupFacetInputs, resolve_own_opponent_unit_grades


@dataclass(frozen=True)
class MatchupThisWeek:
    """The Player Detail tab's matchup summary for the current week.

    own_unit_grade / opponent_unit_grade are the PFF team-grade pair behind this
    player's matchup read (see
    nfl_dfs.matchup.context.resolve_own_opponent_unit_grades for which facet
    pair applies to which position). Both are None when build_player_detail_record
    was called without matchup_facets, or when the relevant team is missing from
    the weekly PFF pull.
    """

    opponent: str
    own_unit_grade: Optional[float]
    opponent_unit_grade: Optional[float]


@dataclass(frozen=True)
class PlayerDetailRecord:
    """One player's row on the Player Detail dashboard tab."""

    player_id: str
    name: str
    position: str
    team: str
    salary: int
    projection: float
    matchup_this_week: MatchupThisWeek


def build_player_detail_record(
    player_id: str,
    name: str,
    position: str,
    team: str,
    opponent: str,
    salary: int,
    projection: float,
    matchup_facets: Optional[MatchupFacetInputs] = None,
) -> PlayerDetailRecord:
    """Assemble one player's Player Detail tab record.

    matchup_facets is optional so this still works for spot checks that don't
    have a weekly PFF facet pull on hand -- in that case
    matchup_this_week.own_unit_grade/opponent_unit_grade are None rather than
    the build failing.
    """
    if matchup_facets is not None:
        grades = resolve_own_opponent_unit_grades(position, team, opponent, matchup_facets)
        own_unit_grade = grades.own_unit_grade
        opponent_unit_grade = grades.opponent_unit_grade
    else:
        own_unit_grade = None
        opponent_unit_grade = None

    return PlayerDetailRecord(
        player_id=player_id,
        name=name,
        position=position,
        team=team,
        salary=salary,
        projection=projection,
        matchup_this_week=MatchupThisWeek(
            opponent=opponent,
            own_unit_grade=own_unit_grade,
            opponent_unit_grade=opponent_unit_grade,
        ),
    )
