"""Trailing QB rushing-opportunity profile (ADR-0030) -- real, descriptive designed-run/scramble/
red-zone rushing aggregation from `nfl_data_py.import_pbp_data()`, one row per (player, team)
covering completed weeks `1..target_week-1` (the same no-look-ahead trailing discipline
`usage_share.py`/`ceiling/signals.py`/`receiving_profile.py` already use).

**Same "descriptive, not predictive" posture as `receiving_profile.py` (ADR-0029), applied to the
one gap that ADR-0028's `CeilingMultiplier` investigation explicitly named and deferred:** "QB
rushing explicitly recommended out of scope (nothing in this codebase computes QB rushing at all
-- comparable in scope to the original `RoleShare` research pass, not a footnote)" and a "degraded
QB fallback" was explicitly rejected as architecturally unsafe (an unshrunk/ungated QB rushing leg
can only ever inflate a QB's ceiling with no self-correcting mechanism). This module sidesteps that
risk entirely by shipping no score, no z-scoring, no shrinkage, and no backtested claim -- just the
real trailing facts (designed-run rate, scramble rate, red-zone/goal-line rushing volume) a person
can use directly, the same posture `receiving_profile.py`'s module docstring lays out. A future
backtested `CeilingMultiplier` QB rushing component (if one is ever built) is separate follow-on
work, not blocked or pre-empted by this module.

**"QB" here means "identified trailing passer," not a roster-position join** -- same convention
`usage_share.py`'s `_trailing_qb_ids`/`QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS` already uses to
keep a mobile QB's scrambles out of the RB role: a player qualifies once they have
`QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS` (5) or more trailing pass attempts, reusing that same
constant and `aggregate_passer_week` rather than duplicating the threshold or adding a new roster-
position dependency.

**Designed run vs. scramble split** uses pbp's own `qb_scramble` flag (confirmed live against real
2025 pbp: populated 0/1 on every `play_type == "run"` row where `rusher_player_id` is a passer;
e.g. J.Hurts logged 99 trailing rush attempts, 41 scrambles / 58 designed runs -- a real, sizeable
split, not noise). `rushing_yards`/`rush_touchdown` are the same pbp columns `usage_share.py`
already relies on being present and reliable.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from nfl_dfs.ingestion.usage_share import (
    QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS,
    RED_ZONE_YARDLINE_100_MAX,
    aggregate_passer_week,
)
from nfl_dfs.normalization.team_aliases import normalize_team

GOAL_LINE_YARDLINE_100_MAX = 5  # standard "inside the 5" goal-line definition, distinct from the
# broader RED_ZONE_YARDLINE_100_MAX=20 this module also reports against.


@dataclass(frozen=True)
class TrailingQbRushingProfile:
    """One identified trailing passer's real trailing rushing-opportunity numbers.
    `designed_run_rate` is `None` exactly when `trailing_rush_attempts` is zero -- never a
    fabricated 0.0 standing in for "no rushing volume yet." `trailing_redzone_rush_attempts`/
    `trailing_goalline_rush_attempts` are raw counts (not shares) -- unlike `RedZoneUsage`'s
    RB/WR role-share reads, there is no team-share denominator computed here, consistent with
    `receiving_profile.py`'s own choice to report rate stats but not team-share stats."""

    player_id: str
    player_name: str | None
    team: str
    trailing_rush_attempts: int
    trailing_designed_runs: int
    trailing_scrambles: int
    designed_run_rate: float | None
    trailing_rushing_yards: int
    trailing_rush_tds: int
    trailing_redzone_rush_attempts: int
    trailing_goalline_rush_attempts: int


def aggregate_trailing_qb_rushing_profile(
    pbp: pd.DataFrame, target_week: int, *, season_type: str | None = "REG"
) -> pd.DataFrame:
    """Trailing (`week < target_week`) QB rushing profile, one row per (team, player_id) for every
    player identified as a trailing passer (`>= QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS` trailing
    pass attempts, `usage_share.py`'s existing convention) who also has at least one trailing rush
    attempt."""
    df = pbp if season_type is None else pbp[pbp["season_type"] == season_type]
    trailing = df[(df["week"] < target_week)]

    passer_week = aggregate_passer_week(pbp, season_type=season_type)
    prior_passer_week = passer_week[passer_week["week"] < target_week]
    qb_totals = prior_passer_week.groupby("player_id", observed=True)["pass_attempts"].sum()
    qb_ids = set(qb_totals[qb_totals >= QB_EXCLUSION_MIN_TRAILING_PASS_ATTEMPTS].index)

    rushes = trailing[
        (trailing["play_type"] == "run")
        & trailing["rusher_player_id"].notna()
        & trailing["rusher_player_id"].isin(qb_ids)
    ]
    if rushes.empty:
        return pd.DataFrame(
            columns=[
                "team", "player_id", "player_name", "trailing_rush_attempts", "trailing_designed_runs",
                "trailing_scrambles", "trailing_rushing_yards", "trailing_rush_tds",
                "trailing_redzone_rush_attempts", "trailing_goalline_rush_attempts",
            ]
        )

    agg = (
        rushes.groupby(["posteam", "rusher_player_id"], observed=True)
        .agg(
            player_name=("rusher_player_name", "first"),
            trailing_rush_attempts=("play_id", "count"),
            trailing_scrambles=("qb_scramble", "sum"),
            trailing_rushing_yards=("rushing_yards", "sum"),
            trailing_rush_tds=("rush_touchdown", "sum"),
            trailing_redzone_rush_attempts=("yardline_100", lambda s: int((s <= RED_ZONE_YARDLINE_100_MAX).sum())),
            trailing_goalline_rush_attempts=("yardline_100", lambda s: int((s <= GOAL_LINE_YARDLINE_100_MAX).sum())),
        )
        .reset_index()
        .rename(columns={"posteam": "team", "rusher_player_id": "player_id"})
    )
    agg["trailing_scrambles"] = agg["trailing_scrambles"].fillna(0).astype(int)
    agg["trailing_rushing_yards"] = agg["trailing_rushing_yards"].fillna(0).astype(int)
    agg["trailing_rush_tds"] = agg["trailing_rush_tds"].fillna(0).astype(int)
    agg["trailing_designed_runs"] = agg["trailing_rush_attempts"] - agg["trailing_scrambles"]
    agg["team"] = agg["team"].map(lambda t: normalize_team("nflverse_schedule", t))
    return agg


def trailing_qb_rushing_profiles(
    pbp: pd.DataFrame, target_week: int, *, season_type: str | None = "REG"
) -> dict[str, TrailingQbRushingProfile]:
    """`aggregate_trailing_qb_rushing_profile`'s output, shaped as the `{player_id: profile}`
    lookup `composition/player_detail.py`'s composer convention expects."""
    df = aggregate_trailing_qb_rushing_profile(pbp, target_week, season_type=season_type)
    profiles: dict[str, TrailingQbRushingProfile] = {}
    for row in df.itertuples(index=False):
        attempts = int(row.trailing_rush_attempts)
        profiles[row.player_id] = TrailingQbRushingProfile(
            player_id=row.player_id,
            player_name=row.player_name,
            team=row.team,
            trailing_rush_attempts=attempts,
            trailing_designed_runs=int(row.trailing_designed_runs),
            trailing_scrambles=int(row.trailing_scrambles),
            designed_run_rate=(row.trailing_designed_runs / attempts) if attempts > 0 else None,
            trailing_rushing_yards=int(row.trailing_rushing_yards),
            trailing_rush_tds=int(row.trailing_rush_tds),
            trailing_redzone_rush_attempts=int(row.trailing_redzone_rush_attempts),
            trailing_goalline_rush_attempts=int(row.trailing_goalline_rush_attempts),
        )
    return profiles
