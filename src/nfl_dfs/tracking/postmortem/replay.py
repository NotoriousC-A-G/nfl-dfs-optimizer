"""Postmortem orchestrator -- loads the week's real slate snapshot, scores every generated agent
lineup AND Chris's own played lineups against real settled points, and assembles one
`PostMortemReport`. Same job as MLB's own `replay.py` (read directly before porting), rebuilt
around this project's real artifacts: `storage/slate_snapshot_store.py`'s snapshot (the 6 agents'
generated lineups + the full pool) and `storage/agent_results_store.py`'s `agent_id="operator"`
rows (what Chris actually played).
"""

from __future__ import annotations

import pandas as pd

from nfl_dfs.ingestion.dst_actual_scoring import aggregate_team_week_dst_points
from nfl_dfs.ingestion.offense_actual_scoring import settled_offensive_points_by_player
from nfl_dfs.normalization.team_aliases import normalize_team
from nfl_dfs.storage.agent_results_store import read_agent_results
from nfl_dfs.storage.slate_snapshot_store import load_latest_slate_snapshot
from nfl_dfs.tracking.name_matching import normalize_player_name, parse_player_token
from nfl_dfs.tracking.postmortem.actual_points import actual_points_by_canonical_id
from nfl_dfs.tracking.postmortem.chalk import build_chalk_lineup
from nfl_dfs.tracking.postmortem.models import LineupOutcome, PlayerOutcome, PostMortemReport
from nfl_dfs.tracking.postmortem.retrospective import compute_process_grade, compute_signal_verdicts, extract_ceiling_patterns

_MISSED_PLAYER_MIN_ACTUAL = 10.0  # disclosed draft floor for "a real high scorer", not backtested


def _agent_lineup_outcomes(snapshot: dict, actual_by_id: dict[str, float]) -> list[LineupOutcome]:
    outcomes = []
    for entry in snapshot.get("agent_lineups", []):
        agent = entry.get("agent") or {}
        lineup = entry.get("lineup") or {}
        players = []
        for p in lineup.get("players", []):
            canonical_id = p.get("canonical_id")
            actual = actual_by_id.get(canonical_id)
            projected = p.get("blended_projection") or 0.0
            players.append(
                PlayerOutcome(
                    canonical_id=canonical_id,
                    display_name=p.get("display_name", canonical_id),
                    team=p.get("team", ""),
                    position=p.get("position", ""),
                    salary=p.get("salary"),
                    projected=projected,
                    actual=actual,
                    delta=(actual - projected) if actual is not None else None,
                )
            )
        unresolved = tuple(p.display_name for p in players if p.actual is None)
        actual_total = None if unresolved else sum(p.actual for p in players)
        projected_total = lineup.get("total_projected_points") or sum(p.projected for p in players)
        outcomes.append(
            LineupOutcome(
                agent_id=agent.get("agent_id", "unknown_agent"),
                label=agent.get("display_name", agent.get("agent_id", "Unknown Agent")),
                players=tuple(players),
                projected_total=round(projected_total, 2),
                actual_total=round(actual_total, 2) if actual_total is not None else None,
                delta=round(actual_total - projected_total, 2) if actual_total is not None else None,
                unresolved_players=unresolved,
            )
        )
    return outcomes


def _operator_lineup_outcomes(
    season: int,
    week: int,
    player_pool: list[dict],
    offensive_points: dict[tuple[str, str], float],
    dst_points: dict[str, float],
) -> list[LineupOutcome]:
    pool_by_name_team = {
        (normalize_player_name((row.get("identity") or {}).get("display_name", "")), row.get("team")): row
        for row in player_pool
    }

    outcomes = []
    for row in read_agent_results(season=season, week=week):
        if row.agent_id != "operator":
            continue
        players = []
        for token in row.players:
            name, position, team = parse_player_token(token)
            key = (normalize_player_name(name), team)
            pool_row = pool_by_name_team.get(key)
            canonical_id = (pool_row.get("identity") or {}).get("canonical_id") if pool_row else None
            salary = pool_row.get("salary") if pool_row else None
            projected = (pool_row.get("projection") if pool_row else None) or 0.0

            if position == "DST":
                actual = dst_points.get(team)
            else:
                actual = offensive_points.get(key)

            players.append(
                PlayerOutcome(
                    canonical_id=canonical_id or f"operator:{key[0]}:{team}",
                    display_name=name,
                    team=team,
                    position=position,
                    salary=salary,
                    projected=projected,
                    actual=actual,
                    delta=(actual - projected) if actual is not None else None,
                )
            )
        unresolved = tuple(p.display_name for p in players if p.actual is None)
        actual_total = None if unresolved else sum(p.actual for p in players)
        projected_total = sum(p.projected for p in players)
        outcomes.append(
            LineupOutcome(
                agent_id="operator",
                label=row.strategy_name,
                players=tuple(players),
                projected_total=round(projected_total, 2),
                actual_total=round(actual_total, 2) if actual_total is not None else None,
                delta=round(actual_total - projected_total, 2) if actual_total is not None else None,
                unresolved_players=unresolved,
            )
        )
    return outcomes


def run_postmortem(season: int, week: int, *, weekly: pd.DataFrame, pbp: pd.DataFrame) -> PostMortemReport | None:
    """Returns `None` when no slate snapshot exists for `(season, week)` -- nothing to build a
    postmortem from (the live script hasn't been run with the snapshot-persistence commit, or
    hasn't been run at all for this week)."""
    snapshot = load_latest_slate_snapshot(season, week)
    if snapshot is None:
        return None

    player_pool = snapshot.get("player_pool", [])
    actual_by_id = actual_points_by_canonical_id(player_pool, season=season, week=week, weekly=weekly, pbp=pbp)

    offensive_points = settled_offensive_points_by_player(weekly, season, week)
    dst_points = {
        (normalize_team("nflverse_schedule", r.team) or r.team): r.dk_points
        for r in aggregate_team_week_dst_points(pbp)
        if r.week == week
    }

    lineup_outcomes = _agent_lineup_outcomes(snapshot, actual_by_id) + _operator_lineup_outcomes(
        season, week, player_pool, offensive_points, dst_points
    )

    scored_lineups = [lo for lo in lineup_outcomes if lo.actual_total is not None]
    our_best = max(scored_lineups, key=lambda lo: lo.actual_total) if scored_lineups else None
    chalk_comparison = build_chalk_lineup(player_pool, actual_by_id, our_best=our_best)

    rostered_ids = {p.canonical_id for lo in lineup_outcomes for p in lo.players}
    all_player_outcomes = [
        PlayerOutcome(
            canonical_id=cid,
            display_name=(row.get("identity") or {}).get("display_name", cid),
            team=row.get("team", ""),
            position=row.get("position", ""),
            salary=row.get("salary"),
            projected=row.get("projection") or 0.0,
            actual=actual_by_id[cid],
            delta=actual_by_id[cid] - (row.get("projection") or 0.0),
        )
        for row in player_pool
        if (cid := (row.get("identity") or {}).get("canonical_id")) in actual_by_id
    ]
    top_performers = tuple(sorted(all_player_outcomes, key=lambda p: p.actual, reverse=True)[:10])
    missed_players = tuple(
        sorted(
            (p for p in all_player_outcomes if p.canonical_id not in rostered_ids and p.actual >= _MISSED_PLAYER_MIN_ACTUAL),
            key=lambda p: p.actual,
            reverse=True,
        )
    )

    verdicts = compute_signal_verdicts(player_pool, actual_by_id)
    process_grade = compute_process_grade(verdicts)
    ceiling_patterns = extract_ceiling_patterns(list(missed_players))

    return PostMortemReport(
        season=season,
        week=week,
        lineup_outcomes=tuple(lineup_outcomes),
        top_performers=top_performers,
        missed_players=missed_players,
        chalk_comparison=chalk_comparison,
        ceiling_patterns=ceiling_patterns,
        process_grade=process_grade,
    )
