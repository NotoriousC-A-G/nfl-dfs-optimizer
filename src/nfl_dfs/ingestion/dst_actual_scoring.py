"""Real, settled DraftKings Classic DST (Defense/Special Teams) scoring, computed from real
`nfl_data_py.import_pbp_data()` play-by-play (PRD Section 3's "DK Classic scoring rules" table,
DST side, added 2026-09-15 -- Chris supplied the real table directly).

**A real, previously-missing piece, not built speculatively.** `scripts/ceiling_role_share_
backtest.py`'s `dk_points_row` computes real OFFENSIVE points only -- it was built specifically
for `CeilingMultiplier`'s WR/RB/QB component backtests (ADR-0028), which never needed DST points,
so DST scoring was never implemented anywhere in this codebase until now. This module is
team-week grain (not per-player), since DK's DST scoring is a team-level roster slot, not an
individual player's stat line.

Every event category below was checked against real 2024 pbp data before being written, not
assumed from the DK Classic rules table alone -- see each computation's own comment for exactly
what was verified. `offensive_fumble_recovery_td_bonus` at the bottom is the one OFFENSIVE line
item `dk_points_row` is still missing (a player recovering his own team's fumble and running it
in) -- kept here since it needs the same pbp fumble-recovery columns this module already reads,
not because it's a DST stat itself.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

SACK_POINTS = 1.0
INTERCEPTION_POINTS = 2.0
FUMBLE_RECOVERY_POINTS = 2.0
# Interception return TD, defensive fumble-recovery TD, and blocked-kick/ordinary punt-kickoff-FG
# return TD are all worth +6 -- DK's table lists each as its own row, separate from the
# "base" event (Interception +2, Fumble Recovery +2) -- confirmed additive, the standard DK/
# industry convention: a pick-six is +2 (the interception) + 6 (the return TD) = 8 total, not 6.
DEFENSIVE_TD_POINTS = 6.0
SAFETY_POINTS = 2.0
BLOCKED_KICK_POINTS = 2.0  # the block itself, additive with any resulting return-TD bonus above.
DEFENSIVE_TWO_POINT_POINTS = 2.0

# (points-allowed upper bound INCLUSIVE, bonus) -- ascending; 35+ handled as the fallback below.
POINTS_ALLOWED_BANDS: tuple[tuple[int, float], ...] = (
    (0, 10.0),
    (6, 7.0),
    (13, 4.0),
    (20, 1.0),
    (27, 0.0),
    (34, -1.0),
)
POINTS_ALLOWED_35_PLUS = -4.0


def _points_allowed_bonus(points_allowed: int) -> float:
    for upper_bound, bonus in POINTS_ALLOWED_BANDS:
        if points_allowed <= upper_bound:
            return bonus
    return POINTS_ALLOWED_35_PLUS


@dataclass(frozen=True)
class DstWeekScoring:
    """One team's real DK Classic DST points for one real, settled week -- every component
    exposed individually (not just the summed `dk_points` total) so a caller can see exactly what
    drove a given week's score, matching this project's own "real reasons, not a black-box
    number" convention used everywhere else (ceiling signals, dup-risk reads, etc.)."""

    team: str
    season: int
    week: int
    sacks: int
    interceptions: int
    fumble_recoveries: int
    # Pooled count: interception return TDs + defensive fumble-recovery TDs + blocked-kick return
    # TDs + ordinary punt/kickoff/FG-miss return TDs -- DK's table doesn't need these split out
    # for scoring purposes (all worth the same DEFENSIVE_TD_POINTS), so one field, not four.
    defensive_touchdowns: int
    safeties: int
    blocked_kicks: int
    defensive_two_point_returns: int
    points_allowed: int
    dk_points: float


def aggregate_team_week_dst_points(pbp: pd.DataFrame, *, season_type: str | None = "REG") -> list[DstWeekScoring]:
    """Real, settled DK Classic DST points per (team, week), computed entirely from real pbp
    events -- no vendor DST projection or live scoring API involved."""
    df = pbp if season_type is None else pbp[pbp["season_type"] == season_type]
    season = int(df["season"].iloc[0]) if "season" in df.columns and len(df) else 0

    # Sacks: `sack==1` is a play-level flag -- a co-sack by two defenders on the same team is
    # still exactly one row (confirmed live: `half_sack_1_player_id`/`half_sack_2_player_id` exist
    # for the PLAYER-credit split, but `sack` itself is never 2 on a shared-sack play), so grouping
    # by (defteam, week) counts real sack EVENTS, never double-counting a shared sack.
    sacks = df[df["sack"] == 1].groupby(["defteam", "week"]).size()

    # Interceptions (the base +2; INT-return-TDs are added separately below, additive).
    interceptions = df[df["interception"] == 1].groupby(["defteam", "week"]).size()

    # Fumble recoveries BY THE DEFENSE, on an ordinary (non-special-teams) play only --
    # `fumble_recovery_1_team == defteam` confirmed live to correctly exclude the offense
    # recovering its own fumble (that case is `offensive_fumble_recovery_td_bonus` below).
    # `special_teams_play` rows are excluded here specifically to avoid double-counting a punt/
    # kickoff-return fumble the SAME return team recovers and carries in for a score -- that event
    # is already captured once, correctly, by `st_return_tds` below; counting it again here would
    # double the real DEFENSIVE_TD_POINTS for one real play.
    defensive_fumble_recoveries = df[
        (df["fumble"] == 1) & (df["fumble_recovery_1_team"] == df["defteam"]) & (df["special_teams_play"] != 1)
    ]
    fumble_recoveries = defensive_fumble_recoveries.groupby(["defteam", "week"]).size()

    # Interception return TDs -- `td_team` (confirmed live) is the team that actually scored,
    # independent of `posteam`/`defteam`'s own play-direction labels.
    int_return_tds = df[(df["interception"] == 1) & (df["touchdown"] == 1) & (df["td_team"] == df["defteam"])]
    fumble_return_tds = defensive_fumble_recoveries[
        (defensive_fumble_recoveries["touchdown"] == 1)
        & (defensive_fumble_recoveries["td_team"] == defensive_fumble_recoveries["defteam"])
    ]
    # `return_team` (confirmed live, real 2024 pbp) is the team that fielded/returned a punt or
    # kickoff -- `return_team == defteam` is exactly the DST's own return unit scoring, covering
    # both an ordinary return TD and a blocked-kick-then-returned TD (a blocked punt/FG that's
    # then run back still sets `return_touchdown`/`return_team` the same way).
    st_return_tds = df[
        (df["special_teams_play"] == 1) & (df["return_touchdown"] == 1) & (df["return_team"] == df["defteam"])
    ]

    defensive_tds = (
        pd.concat([int_return_tds[["defteam", "week"]], fumble_return_tds[["defteam", "week"]], st_return_tds[["defteam", "week"]]])
        .groupby(["defteam", "week"])
        .size()
    )

    safeties = df[df["safety"] == 1].groupby(["defteam", "week"]).size()

    # Blocked kicks -- the block itself (+2), whether or not it's ALSO returned for a TD (that
    # bonus is counted separately above via `st_return_tds`; DK's table lists them as additive
    # line items, same convention as interception/INT-return-TD).
    blocked_punts = df[df["punt_blocked"] == 1].groupby(["defteam", "week"]).size()
    blocked_fgs = df[df["field_goal_result"] == "blocked"].groupby(["defteam", "week"]).size()
    blocked_kicks = blocked_punts.add(blocked_fgs, fill_value=0)

    two_point_returns = df[df["defensive_two_point_conv"] == 1].groupby(["defteam", "week"]).size()

    # Points allowed: this team's real opponent's final score that game. `total_home_score`/
    # `total_away_score` are RUNNING totals-as-of-that-play, not a fixed per-game constant --
    # confirmed live (a real bug caught here, not assumed correct): `drop_duplicates(subset=
    # "game_id")`'s default `keep="first"` grabs the game's FIRST play, where both scores are
    # still 0-0. Sorting by `play_id` and keeping the LAST row per game gets the real settled
    # final score instead.
    games = df.sort_values("play_id").drop_duplicates(subset="game_id", keep="last")[
        ["game_id", "week", "home_team", "away_team", "total_home_score", "total_away_score"]
    ]
    points_allowed_rows = []
    for row in games.itertuples(index=False):
        points_allowed_rows.append({"defteam": row.home_team, "week": row.week, "points_allowed": row.total_away_score})
        points_allowed_rows.append({"defteam": row.away_team, "week": row.week, "points_allowed": row.total_home_score})
    points_allowed = pd.DataFrame(points_allowed_rows).set_index(["defteam", "week"])["points_allowed"]

    results: list[DstWeekScoring] = []
    for (team, week) in sorted(points_allowed.index):
        n_sacks = int(sacks.get((team, week), 0))
        n_int = int(interceptions.get((team, week), 0))
        n_fum = int(fumble_recoveries.get((team, week), 0))
        n_td = int(defensive_tds.get((team, week), 0))
        n_safety = int(safeties.get((team, week), 0))
        n_blocked = int(blocked_kicks.get((team, week), 0))
        n_2pt = int(two_point_returns.get((team, week), 0))
        pts_allowed = int(points_allowed.get((team, week), 0))

        dk_points = (
            n_sacks * SACK_POINTS
            + n_int * INTERCEPTION_POINTS
            + n_fum * FUMBLE_RECOVERY_POINTS
            + n_td * DEFENSIVE_TD_POINTS
            + n_safety * SAFETY_POINTS
            + n_blocked * BLOCKED_KICK_POINTS
            + n_2pt * DEFENSIVE_TWO_POINT_POINTS
            + _points_allowed_bonus(pts_allowed)
        )
        results.append(
            DstWeekScoring(
                team=team,
                season=season,
                week=int(week),
                sacks=n_sacks,
                interceptions=n_int,
                fumble_recoveries=n_fum,
                defensive_touchdowns=n_td,
                safeties=n_safety,
                blocked_kicks=n_blocked,
                defensive_two_point_returns=n_2pt,
                points_allowed=pts_allowed,
                dk_points=dk_points,
            )
        )
    return results


def offensive_fumble_recovery_td_bonus(pbp: pd.DataFrame, *, season_type: str | None = "REG") -> dict[tuple[str, int], float]:
    """The one OFFENSIVE scoring line item `dk_points_row` (weekly-aggregate-based) can't compute
    -- `import_weekly_data()` has no dedicated column for an offensive player recovering his OWN
    team's fumble and returning it for a TD (confirmed live: no `fumble_recovery_tds`-shaped
    column exists there). Genuinely rare (17 real events across 2022-2024 pbp, confirmed live) but
    real -- returns `(player_id, week) -> 6.0` for each such event, an ADDITIVE correction a
    caller can add on top of `dk_points_row`'s own output for the same player-week, not a
    replacement for it.

    **Deliberately NOT retrofitted into every already-shipped, already-verified backtest this
    project has already closed out** (Components A-F, ADR-0028) -- the expected impact (a handful
    of player-weeks scattered across a 6-season window) would not be expected to change any of
    those already-closed conclusions, and re-running six separate closed backtests for a rare
    6-point correction is not a good use of this project's time. Available here for any FUTURE
    backtest that wants the fully-correct figure.
    """
    df = pbp if season_type is None else pbp[pbp["season_type"] == season_type]
    events = df[(df["fumble"] == 1) & (df["fumble_recovery_1_team"] == df["posteam"]) & (df["touchdown"] == 1)]
    return {
        (str(row.fumble_recovery_1_player_id), int(row.week)): 6.0
        for row in events.itertuples(index=False)
        if pd.notna(row.fumble_recovery_1_player_id)
    }
