"""Real settled DK points for every player in a slate snapshot's full pool, keyed by
`canonical_id` -- the one join every other postmortem module (chalk comparison, retrospective
signal verdicts, ceiling-pattern extraction, agent-lineup outcomes) reads from.

**Distinct from `tracking/agent_results_collector.py`'s own join.** That module matches
`agent_results.csv`'s free-text `players` tokens ("Name (POS-TEAM)") against real settled data --
the only option there, since that CSV carries no canonical_id. A slate snapshot's `player_pool`
(`storage/slate_snapshot_store.py`) DOES carry a real `identity.canonical_id` per row (straight
from `PlayerDetailRecord`), so this module can build one canonical_id-keyed map for the whole
pool up front and let every downstream consumer do an O(1) lookup, rather than re-running the
name/team match per lineup per player. The underlying settled-data sources (`ingestion/
offense_actual_scoring.py`, `ingestion/dst_actual_scoring.py`) and the name-normalization
(`tracking/name_matching.py`) are shared with that module -- one real source of truth for "what
actually happened," two different join keys into it depending on what the caller has on hand.
"""

from __future__ import annotations

import pandas as pd

from nfl_dfs.ingestion.dst_actual_scoring import aggregate_team_week_dst_points
from nfl_dfs.ingestion.offense_actual_scoring import (
    settled_offensive_box_scores_by_player,
    settled_offensive_points_by_player,
)
from nfl_dfs.normalization.team_aliases import normalize_team
from nfl_dfs.tracking.name_matching import normalize_player_name


def actual_points_by_canonical_id(
    player_pool: list[dict], *, season: int, week: int, weekly: pd.DataFrame, pbp: pd.DataFrame
) -> dict[str, float]:
    """`player_pool` is a slate snapshot's own `player_pool` list (asdict'd `PlayerDetailRecord`
    rows). Returns real settled DK points for every canonical_id that resolves -- a player with no
    match (bye week, unmatched name, inactive/didn't play) is simply absent from the returned
    dict, never defaulted to 0.0 (see every downstream module's own "absence, not zero" handling).
    """
    offensive_points = settled_offensive_points_by_player(weekly, season, week)
    dst_points = {
        (normalize_team("nflverse_schedule", row.team) or row.team): row.dk_points
        for row in aggregate_team_week_dst_points(pbp)
        if row.week == week
    }

    resolved: dict[str, float] = {}
    for row in player_pool:
        identity = row.get("identity") or {}
        canonical_id = identity.get("canonical_id")
        display_name = identity.get("display_name")
        team = row.get("team")
        position = row.get("position")
        if not canonical_id or not display_name or not team:
            continue

        if position == "DST":
            if team in dst_points:
                resolved[canonical_id] = dst_points[team]
            continue

        key = (normalize_player_name(display_name), team)
        if key in offensive_points:
            resolved[canonical_id] = offensive_points[key]

    return resolved


def box_score_lines_by_canonical_id(player_pool: list[dict], *, season: int, week: int, weekly: pd.DataFrame) -> dict[str, str]:
    """Real, settled box-score summary line (see `offense_actual_scoring.format_box_score_line`)
    per canonical_id -- same join shape as `actual_points_by_canonical_id`, DST rows excluded (DST
    points come from `dst_actual_scoring.py`'s team-week grain, not a per-player box score, so
    there's no real line to show). A canonical_id with no offensive stats row this week (bye,
    inactive, unmatched name) is simply absent, never defaulted to a placeholder string."""
    lines = settled_offensive_box_scores_by_player(weekly, season, week)

    resolved: dict[str, str] = {}
    for row in player_pool:
        identity = row.get("identity") or {}
        canonical_id = identity.get("canonical_id")
        display_name = identity.get("display_name")
        team = row.get("team")
        if not canonical_id or not display_name or not team or row.get("position") == "DST":
            continue
        key = (normalize_player_name(display_name), team)
        if key in lines:
            resolved[canonical_id] = lines[key]

    return resolved
