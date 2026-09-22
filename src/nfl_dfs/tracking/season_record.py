"""Season-to-date performance record per agent (and the operator) -- Chris, 2026-09-22: "We
should build the agent and operator records." Direct analog of the sister MLB project's own
"Agent Evolution" panel (`postmortem_renderer/__init__.py`'s `_render_agent_evolution_panel`,
seen live via the browser tool), scoped to what's actually derivable today: a handful of weeks,
not MLB's many months of settled slates.

**Computed on demand from the two real stores that already exist, not a third store of its own.**
`agent_results.csv` (`storage/agent_results_store.py`) has every agent's and the operator's
per-week internal score/rank; `contest_results.csv` (`storage/contest_results_store.py`) has the
operator's real DK contest outcomes. A season record is a pure aggregation over rows already
sitting in those two files -- recomputing it fresh each call (nothing cached) means there's only
ever one source of truth for the underlying numbers, matching this project's own "don't build a
second thing that can drift from the first" posture used everywhere else.

**"0-1" is a real, honest record, not a placeholder.** Two weeks into a season, most records will
be 0-1 or 1-1 -- disclosed as exactly that (`weeks_tracked` is always in the output), not padded
or hidden. A caller showing a 1-week record should say "1 week", not silently imply a longer track
record than actually exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_dfs.storage.agent_results_store import AgentResultRow, read_agent_results
from nfl_dfs.storage.contest_results_store import ContestResult, read_contest_results


@dataclass(frozen=True)
class SeasonRecord:
    """One agent's (or the operator's) record across every week logged so far this season."""

    agent_id: str  # a real NflAgentConstructor.agent_id slug, or "operator"
    weeks_tracked: int  # distinct weeks with >=1 scored row for this agent_id
    wins: int  # weeks this agent_id had the single best total_dk_score among that week's rows
    top_three: int  # weeks this agent_id finished top 3 by total_dk_score that week
    avg_delta: float | None  # mean (actual - proj) across scored rows; None if none scored yet
    best_week: tuple[int, float] | None  # (week, total_dk_score) of the single best scored week
    worst_week: tuple[int, float] | None
    # Real DK contest entries -- operator only; always (0, 0) for the 6 agents, which are never
    # actually entered into a contest themselves.
    contest_entries: int = 0
    contest_cashes: int = 0


def compute_season_records(season: int, *, agent_path=None, contest_path=None) -> list[SeasonRecord]:
    """One `SeasonRecord` per distinct `agent_id` seen in `agent_results.csv` for `season` (the 6
    real agents plus "operator" once anything is logged for it), across every week logged so far.
    """
    all_rows = read_agent_results(season=season, path=agent_path)
    contest_rows = read_contest_results(season=season, path=contest_path)

    by_agent: dict[str, list[AgentResultRow]] = {}
    for row in all_rows:
        by_agent.setdefault(row.agent_id, []).append(row)

    # Per-week ranking (across ALL agent_ids that week) -- needed to know who "won" a given week,
    # not just each agent's own row in isolation.
    by_week: dict[int, list[AgentResultRow]] = {}
    for row in all_rows:
        by_week.setdefault(row.week, []).append(row)
    winner_by_week: dict[int, str] = {}
    top_three_by_week: dict[int, set[str]] = {}
    for week, rows in by_week.items():
        scored = [r for r in rows if r.total_dk_score is not None]
        if not scored:
            continue
        ranked = sorted(scored, key=lambda r: -r.total_dk_score)
        winner_by_week[week] = ranked[0].agent_id
        top_three_by_week[week] = {r.agent_id for r in ranked[:3]}

    contest_by_lineup: dict[str, list[ContestResult]] = {}
    for c in contest_rows:
        contest_by_lineup.setdefault(c.lineup_label, []).append(c)

    records = []
    for agent_id, rows in by_agent.items():
        weeks = {r.week for r in rows}
        scored = [r for r in rows if r.total_dk_score is not None]
        deltas = [r.total_dk_score - r.proj_total for r in scored]
        wins = sum(1 for w in weeks if winner_by_week.get(w) == agent_id)
        top3 = sum(1 for w in weeks if agent_id in top_three_by_week.get(w, set()))

        best_week = max(((r.week, r.total_dk_score) for r in scored), key=lambda t: t[1], default=None)
        worst_week = min(((r.week, r.total_dk_score) for r in scored), key=lambda t: t[1], default=None)

        # Operator rows use strategy_name (e.g. "L1") as the contest-results join key, not
        # agent_id -- collect across every strategy_name this agent_id used this season.
        entries = 0
        cashes = 0
        if agent_id == "operator":
            for r in rows:
                for c in contest_by_lineup.get(r.strategy_name, []):
                    entries += 1
                    cashes += int(c.cashed)

        records.append(
            SeasonRecord(
                agent_id=agent_id,
                weeks_tracked=len(weeks),
                wins=wins,
                top_three=top3,
                avg_delta=round(sum(deltas) / len(deltas), 2) if deltas else None,
                best_week=best_week,
                worst_week=worst_week,
                contest_entries=entries,
                contest_cashes=cashes,
            )
        )

    records.sort(key=lambda r: (-r.wins, -r.top_three, -(r.avg_delta or float("-inf"))))
    return records
