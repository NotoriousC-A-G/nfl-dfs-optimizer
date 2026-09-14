"""Trailing receiving-opportunity profile (ADR-0029) -- real, descriptive air-yards/depth-of-
target/yards-after-catch aggregation from `nfl_data_py.import_pbp_data()`, one row per (player,
team) covering completed weeks `1..target_week-1` (the same no-look-ahead trailing discipline
`usage_share.py`/`ceiling/signals.py` already use).

**Deliberately not a ceiling signal.** `ceiling/signals.py`'s Component C (trailing aDOT) was
backtested against real DK outcomes and came back a clean null for both WR and TE (ADR-0028) --
aDOT alone does not predict which players have a statistically real edge in aggregate. But Chris's
own framing, after that result: these numbers are valuable independent of whether they move a
predictive multiplier -- they're what a person actually wants to see when choosing between two
similarly-priced players, especially the "have to reach for salary-cap value" case, to judge *what
kind* of opportunity each one is getting (a real target share with real depth and real YAC upside,
vs. token/short-range volume). This module produces exactly those real numbers, with no z-scoring,
no shrinkage, no backtested claim attached -- a read-model over real trailing facts, the same
"give the person the real inputs, not a black-box score" posture the rest of the Player Detail tab
already follows for role share, snap share, and man/zone splits.

Red-zone opportunity (the other half of Chris's ask) already exists as its own section
(`RedZoneUsage`, ADR-0022) -- not duplicated here.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from nfl_dfs.normalization.team_aliases import normalize_team


@dataclass(frozen=True)
class TrailingReceivingProfile:
    """One player's real trailing receiving-opportunity numbers. `trailing_adot`/
    `trailing_yac_per_reception` are `None` exactly when their own denominator (targets/receptions)
    is zero -- never a fabricated 0.0 standing in for "no data yet"."""

    player_id: str
    player_name: str | None
    team: str
    trailing_targets: int
    trailing_receptions: int
    trailing_air_yards: int
    trailing_adot: float | None
    trailing_yac_per_reception: float | None


def aggregate_trailing_receiving_profile(
    pbp: pd.DataFrame, target_week: int, *, season_type: str | None = "REG"
) -> pd.DataFrame:
    """Trailing (`week < target_week`) receiving profile, one row per (team, player_id):
    `trailing_targets`, `trailing_receptions`, `trailing_air_yards` (sum), `trailing_adot` (mean
    air yards per target), `trailing_yac_per_reception` (mean yards after catch per completed
    reception -- `yards_after_catch` is only recorded on completions, confirmed live against real
    pbp). `air_yards`/`yards_after_catch` are the same real pbp columns already confirmed present
    and used by `ceiling/signals.py`'s `_aggregate_trailing_adot` -- no new ingestion.
    """
    df = pbp if season_type is None else pbp[pbp["season_type"] == season_type]
    trailing = df[(df["week"] < target_week) & (df["play_type"] == "pass") & df["receiver_player_id"].notna()]

    targets = (
        trailing.groupby(["posteam", "receiver_player_id"], observed=True)
        .agg(
            trailing_targets=("play_id", "count"),
            trailing_air_yards=("air_yards", "sum"),
            trailing_adot=("air_yards", "mean"),
            player_name=("receiver_player_name", "first"),
        )
        .reset_index()
    )

    completions = trailing[trailing["complete_pass"] == 1]
    yac = (
        completions.groupby(["posteam", "receiver_player_id"], observed=True)
        .agg(trailing_receptions=("play_id", "count"), trailing_yac_per_reception=("yards_after_catch", "mean"))
        .reset_index()
    )

    merged = targets.merge(yac, on=["posteam", "receiver_player_id"], how="left")
    merged["trailing_receptions"] = merged["trailing_receptions"].fillna(0).astype(int)
    merged["trailing_air_yards"] = merged["trailing_air_yards"].fillna(0).astype(int)
    merged = merged.rename(columns={"posteam": "team", "receiver_player_id": "player_id"})
    merged["team"] = merged["team"].map(lambda t: normalize_team("nflverse_schedule", t))
    return merged


def trailing_receiving_profiles(
    pbp: pd.DataFrame, target_week: int, *, season_type: str | None = "REG"
) -> dict[str, TrailingReceivingProfile]:
    """`aggregate_trailing_receiving_profile`'s output, shaped as the `{player_id: profile}` lookup
    `composition/player_detail.py`'s composer convention expects."""
    df = aggregate_trailing_receiving_profile(pbp, target_week, season_type=season_type)
    profiles: dict[str, TrailingReceivingProfile] = {}
    for row in df.itertuples(index=False):
        profiles[row.player_id] = TrailingReceivingProfile(
            player_id=row.player_id,
            player_name=row.player_name,
            team=row.team,
            trailing_targets=int(row.trailing_targets),
            trailing_receptions=int(row.trailing_receptions),
            trailing_air_yards=int(row.trailing_air_yards),
            trailing_adot=float(row.trailing_adot) if pd.notna(row.trailing_adot) else None,
            trailing_yac_per_reception=(
                float(row.trailing_yac_per_reception) if pd.notna(row.trailing_yac_per_reception) else None
            ),
        )
    return profiles
