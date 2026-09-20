"""Backfills `total_dk_score`/`lineup_rank` on already-logged `agent_results.csv` rows once a
week's games have settled -- the "forward log now, join later" second half `agent_results_store.py`
itself defers (see that module's docstring), and the direct NFL port of MLB's own `--collect-
results` step (`mlb_dfs/tracking/agent_results.py`'s `collect_agent_results`/
`collect_operator_agent_result`, read directly to confirm the real mechanics before porting).

**`boom` is deliberately left `None` here, not computed.** MLB's own `_is_boom` needs a real
per-slate score-tier distribution (`agent_evolution.BOOM_TIERS`/`classify_score_tier`) built from
many settled slates over time -- nothing like that exists for NFL yet, and MLB's own module
docstring explicitly warns that the naive alternative (`dk_score >= 2 * proj_total`) is
"structurally degenerate" for a multi-player lineup sum. Fabricating an NFL threshold here on zero
settled weeks would repeat exactly the mistake that comment warns against. Once enough NFL weeks
are logged to build a real tier distribution, that's a separate, later piece -- not guessed at now.

**Real settled points, two disjoint sources, joined by name/team:**
- Offensive players: `ingestion/offense_actual_scoring.py`'s `settled_offensive_points_by_player`,
  over a live `nfl_data_py.import_weekly_data([season])` pull.
- DST rows: `ingestion/dst_actual_scoring.py`'s `aggregate_team_week_dst_points`, over a live
  `nfl_data_py.import_pbp_data([season])` pull -- team-week grain, no player-name join needed.

**A row's `total_dk_score` is only ever written from a COMPLETE match.** If any one roster slot
fails to resolve (a name-matching miss, a bye-week/inactive player, etc.), the row is left
unscored and the miss is surfaced in `ScoreCollectionResult.unresolved` -- summing the players that
DID match and silently treating the rest as 0 would produce a real number that's quietly wrong,
the same "silent no-op"-shaped bug MLB's own agent-tracking history warns against (see
`agent_results_store.py`'s module docstring).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import pandas as pd

from nfl_dfs.ingestion.dst_actual_scoring import aggregate_team_week_dst_points
from nfl_dfs.ingestion.offense_actual_scoring import settled_offensive_points_by_player
from nfl_dfs.normalization.team_aliases import normalize_team
from nfl_dfs.storage.agent_results_store import AgentResultRow, overwrite_agent_results, read_agent_results
from nfl_dfs.tracking.name_matching import normalize_player_name, parse_player_token


@dataclass(frozen=True)
class ScoreCollectionResult:
    """What `score_and_backfill_agent_results` actually did, for the caller (a live script) to
    print/inspect -- no silent partial success."""

    scored: tuple[AgentResultRow, ...] = field(default_factory=tuple)
    # (agent_id, strategy_name, unresolved player tokens) for every row that could NOT be fully
    # scored -- surfaced, never silently dropped.
    unresolved: tuple[tuple[str, str, tuple[str, ...]], ...] = field(default_factory=tuple)


def _score_one_row(
    row: AgentResultRow,
    offensive_points: dict[tuple[str, str], float],
    dst_points: dict[str, float],
) -> tuple[float, tuple[str, ...]]:
    """Returns (total, missing_tokens). `total` is only meaningful when `missing_tokens` is empty."""
    total = 0.0
    missing: list[str] = []
    for token in row.players:
        name, position, team = parse_player_token(token)
        if position == "DST":
            if team in dst_points:
                total += dst_points[team]
            else:
                missing.append(token)
            continue

        key = (normalize_player_name(name), team)
        if key in offensive_points:
            total += offensive_points[key]
        else:
            missing.append(token)

    return total, tuple(missing)


def score_and_backfill_agent_results(
    season: int,
    week: int,
    *,
    weekly: pd.DataFrame,
    pbp: pd.DataFrame,
    path=None,
) -> ScoreCollectionResult:
    """Scores every `(season, week)` row already in `agent_results.csv` against real settled data
    and rewrites the file with `total_dk_score`/`lineup_rank` backfilled (rows for OTHER weeks are
    left untouched). `weekly`/`pbp` are raw `nfl_data_py.import_weekly_data([season])` /
    `import_pbp_data([season])` pulls -- fetched by the caller (a live script) so this function
    stays testable against fixtures, no live network call inside it.
    """
    offensive_points = settled_offensive_points_by_player(weekly, season, week)
    dst_points = {
        (normalize_team("nflverse_schedule", row.team) or row.team): row.dk_points
        for row in aggregate_team_week_dst_points(pbp)
        if row.week == week
    }

    all_rows = read_agent_results(path=path)
    this_week = [r for r in all_rows if r.season == season and r.week == week]
    other_weeks = [r for r in all_rows if not (r.season == season and r.week == week)]

    scored_rows: list[AgentResultRow] = []
    unresolved: list[tuple[str, str, tuple[str, ...]]] = []
    for row in this_week:
        total, missing = _score_one_row(row, offensive_points, dst_points)
        if missing:
            unresolved.append((row.agent_id, row.strategy_name, missing))
            scored_rows.append(row)  # left as-is -- total_dk_score stays whatever it was (None, typically)
        else:
            scored_rows.append(replace(row, total_dk_score=round(total, 2)))

    # lineup_rank: 1 = best, computed only across rows that resolved a real total_dk_score this
    # pass (an unresolved row has nothing to rank by).
    ranked = sorted(
        (r for r in scored_rows if r.total_dk_score is not None), key=lambda r: r.total_dk_score, reverse=True
    )
    rank_by_key = {(r.agent_id, r.strategy_name): i + 1 for i, r in enumerate(ranked)}
    final_rows = [
        replace(r, lineup_rank=rank_by_key[(r.agent_id, r.strategy_name)])
        if (r.agent_id, r.strategy_name) in rank_by_key
        else r
        for r in scored_rows
    ]

    overwrite_agent_results(other_weeks + final_rows, path=path)
    return ScoreCollectionResult(scored=tuple(final_rows), unresolved=tuple(unresolved))
