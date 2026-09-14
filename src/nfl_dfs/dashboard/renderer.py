"""Pre-lock review dashboard rendering (docs/PRD.md Section 8/10, UI/UX).

Player Detail tab
------------------
Each row is one nfl_dfs.composition.player_detail.PlayerDetailRecord.
`matchup_this_week.own_unit_grade` / `matchup_this_week.opponent_unit_grade`
render as their own "Own Grade" / "Opp Grade" columns -- the PFF team-grade pair
behind that player's matchup read this week (see
nfl_dfs.matchup.context.resolve_own_opponent_unit_grades for which facet pair
applies per position: run-block/run-defense for RB, pass-block/pass-rush for
QB, receiving-scheme/coverage-scheme for WR/TE).

A column showing "--" for every row means matchup_facets wasn't threaded
through the build_player_detail_record calls that assembled this pool --
that's a wiring gap in whatever assembles the dashboard's player pool
(e.g. scripts/live_integration_check_dashboard.py), not something this
renderer can fix by explaining the field in a legend instead of rendering it.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from nfl_dfs.composition.player_detail import PlayerDetailRecord

_COLUMNS = ("Player", "Pos", "Team", "Opp", "Salary", "Proj", "Own Grade", "Opp Grade")


def _format_grade(grade: Optional[float]) -> str:
    return f"{grade:.1f}" if grade is not None else "--"


def _render_player_detail_tab(records: Iterable[PlayerDetailRecord]) -> str:
    """Render the Player Detail tab as a plain-text, column-aligned table."""
    rows: List[List[str]] = [list(_COLUMNS)]
    for record in records:
        rows.append(
            [
                record.name,
                record.position,
                record.team,
                record.matchup_this_week.opponent,
                f"${record.salary:,}",
                f"{record.projection:.1f}",
                _format_grade(record.matchup_this_week.own_unit_grade),
                _format_grade(record.matchup_this_week.opponent_unit_grade),
            ]
        )

    widths = [max(len(row[col]) for row in rows) for col in range(len(_COLUMNS))]
    lines = ["  ".join(cell.ljust(widths[col]) for col, cell in enumerate(row)) for row in rows]
    return "\n".join(lines)
