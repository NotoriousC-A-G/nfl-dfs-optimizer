#!/usr/bin/env python3
"""Manual smoke check: assemble the Player Detail dashboard tab for a sample slate.

This is a script to run and eyeball, not an automated test. It does not call the
real PFF API -- Phase 0 ingestion (docs/PRD.md Section 12) isn't built yet, so
`_sample_matchup_facets()` stands in for the weekly PFF grade pull until the
Data Integration Engineer wires a real fetch in. Everything downstream of that
one function (build_player_detail_record through _render_player_detail_tab)
already consumes MatchupFacetInputs, so swapping in a real fetch there is the
only change needed later -- nothing else in this script has to move.
"""

from __future__ import annotations

from typing import List, Tuple

from nfl_dfs.composition.player_detail import PlayerDetailRecord, build_player_detail_record
from nfl_dfs.dashboard.renderer import _render_player_detail_tab
from nfl_dfs.matchup.context import MatchupFacetInputs

# (player_id, name, position, team, opponent, salary, projection)
_SAMPLE_PLAYERS: List[Tuple[str, str, str, str, str, int, float]] = [
    ("1", "Sample QB", "QB", "BUF", "MIA", 8200, 22.5),
    ("2", "Sample RB", "RB", "BUF", "MIA", 7600, 18.0),
    ("3", "Sample WR", "WR", "MIA", "BUF", 7000, 15.5),
    ("4", "Sample TE", "TE", "BUF", "MIA", 4400, 9.5),
    ("5", "Sample DST", "DST", "BUF", "MIA", 2800, 8.0),
]


def _sample_matchup_facets() -> MatchupFacetInputs:
    """Stand-in for the weekly PFF grade pull -- see module docstring."""
    return MatchupFacetInputs(
        offense_run_blocking={"BUF": 78.0, "MIA": 65.0},
        defense_run={"BUF": 70.0, "MIA": 60.0},
        offense_pass_blocking={"BUF": 74.0, "MIA": 68.0},
        defense_pass_rush={"BUF": 72.0, "MIA": 66.0},
        defense_coverage_scheme={"BUF": 69.0, "MIA": 71.0},
        receiving_scheme={"BUF": 73.0, "MIA": 70.0},
    )


def build_player_detail_pool() -> List[PlayerDetailRecord]:
    facets = _sample_matchup_facets()
    return [
        build_player_detail_record(
            player_id=player_id,
            name=name,
            position=position,
            team=team,
            opponent=opponent,
            salary=salary,
            projection=projection,
            matchup_facets=facets,
        )
        for player_id, name, position, team, opponent, salary, projection in _SAMPLE_PLAYERS
    ]


def main() -> None:
    records = build_player_detail_pool()
    print(_render_player_detail_tab(records))


if __name__ == "__main__":
    main()
