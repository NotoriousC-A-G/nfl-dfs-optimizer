"""Real, settled DraftKings Classic OFFENSIVE scoring, computed from real `nfl_data_py.
import_weekly_data()` box-score rows (PRD Section 3's "DK Classic scoring rules" table).

**Extracted, not reinvented.** `dk_points_row` below is the exact formula already live-verified in
`scripts/ceiling_role_share_backtest.py:47-76` for `CeilingMultiplier`'s WR/RB/QB component
backtests (ADR-0028) -- that script only ever needed it to compute a trailing median, so the
formula was never pulled into an importable module. `tracking/agent_results_collector.py` (built
2026-09-20 for the agent-performance postmortem) is the first caller that needs it as a real,
per-week SETTLED total (not a trailing baseline), so it's extracted here rather than duplicated a
third time. `scripts/ceiling_role_share_backtest.py` is left as-is (a research script, not
refactored in this round) -- both copies must be kept in sync if the DK scoring rules ever change,
same "small justified duplication over a cross-layer import" tradeoff this project already made
for `_pool_iqr` (`optimizer/lineup.py` vs `agents/scoring.py`).

Still offense-only, same as the original: Offensive Fumble Recovery TD (+6) is NOT computed here
(see `dst_actual_scoring.py`'s `offensive_fumble_recovery_td_bonus` for that rare, additive
correction). Real DST points live entirely in `dst_actual_scoring.py` -- team-week grain, not
per-player, so it can't share this function's row-apply shape.
"""

from __future__ import annotations

import pandas as pd


def dk_points_row(row: pd.Series) -> float:
    """DK Classic OFFENSIVE scoring formula, computed from one `import_weekly_data()` row's real
    box-score columns. See module docstring -- verbatim from `scripts/ceiling_role_share_
    backtest.py`'s own `dk_points_row`."""
    pts = 0.0
    pts += row["passing_yards"] * 0.04
    pts += row["passing_tds"] * 4
    pts += row["interceptions"] * -1
    pts += 3.0 if row["passing_yards"] >= 300 else 0.0
    pts += row["rushing_yards"] * 0.1
    pts += row["rushing_tds"] * 6
    pts += 3.0 if row["rushing_yards"] >= 100 else 0.0
    pts += row["receptions"] * 1
    pts += row["receiving_yards"] * 0.1
    pts += row["receiving_tds"] * 6
    pts += 3.0 if row["receiving_yards"] >= 100 else 0.0
    pts += (row["rushing_fumbles_lost"] + row["receiving_fumbles_lost"] + row["sack_fumbles_lost"]) * -1
    pts += (row["passing_2pt_conversions"] + row["rushing_2pt_conversions"] + row["receiving_2pt_conversions"]) * 2
    return pts


def settled_offensive_points_by_player(
    weekly: pd.DataFrame, season: int, week: int, *, season_type: str | None = "REG"
) -> dict[tuple[str, str], float]:
    """Real, settled DK offensive points for one (season, week), keyed by (normalized display
    name, team) -- the same join shape `tracking/agent_results_collector.py` needs to score a
    played/generated lineup's roster. `weekly` is a raw `nfl_data_py.import_weekly_data([season])`
    frame; this function does the season/week/season_type filtering itself so callers don't
    silently forget one of the three.
    """
    from nfl_dfs.normalization.team_aliases import normalize_team
    from nfl_dfs.tracking.name_matching import normalize_player_name

    df = weekly[(weekly["season"] == season) & (weekly["week"] == week)]
    if season_type is not None:
        df = df[df["season_type"] == season_type]

    points: dict[tuple[str, str], float] = {}
    for _, row in df.iterrows():
        name_key = normalize_player_name(str(row["player_display_name"]))
        # import_weekly_data's team column uses the same non-canonical codes as the nflverse ID
        # crosswalk (GBP/JAC/KCC/LVR/NEP/NOS/SFO/TBB) -- both are nflverse's own convention, so
        # the "crosswalk" alias table applies here too, not a new nflverse_weekly table.
        raw_team = str(row["recent_team"]).upper()
        team_key = normalize_team("crosswalk", raw_team) or raw_team
        points[(name_key, team_key)] = dk_points_row(row)
    return points
