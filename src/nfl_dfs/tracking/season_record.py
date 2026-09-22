"""Season-to-date performance record per agent AND per operator-played lineup -- Chris,
2026-09-22: "We should build the agent and operator records" (then, correcting the first version
of this: "we should show the operator L1, L2, and L3 as individual agents as opposed to all the
operator lineups being grouped as 1"). Direct analog of the sister MLB project's own "Agent
Evolution" panel (`postmortem_renderer/__init__.py`'s `_render_agent_evolution_panel`, seen live
via the browser tool), scoped to what's actually derivable today: a handful of weeks, not MLB's
many months of settled slates.

**Tracked entities: L1, L2, L3 (individually, not pooled as "operator"), plus the 6 real agents.**
`agent_results.csv` stores every operator lineup under the literal `agent_id="operator"` with
`strategy_name` carrying the real label ("L1", "L2", ...) -- grouping by `agent_id` alone (the
first version of this module) collapsed three lineups with wildly different real performance into
one misleading average. This version groups by `strategy_name` for operator rows and by
`agent_id` for the 6 real agents, so "L1" and "Chalk Anchor" are peers in the same table.

**"Win" means "would have cashed," not "beat the other lineups internally."** Chris, 2026-09-22:
"I want the win to be more like they would have cashed." For L1/L2/L3, that's REAL: `contest_
results.csv` already has an exact `cashed` flag per real DK entry. For the 6 agents -- never
actually entered into a contest -- "would have cashed" is answered by `tracking/contest_placement
_estimate.py`'s interpolation against the SAME real contests Chris actually entered that week (his
own words: "as if each of the agents had been entered in the contests I had entered"). Every
record carries its own `basis` ("real" or "estimated") so a viewer never mistakes one for the
other; an agent's `contest_entries` is 0 for any week with no multi-entry contest to estimate
against (a real, disclosed gap -- see that module's own docstring for why single-entry weeks
aren't guessed at).

**Computed on demand from the two real stores that already exist, not a third store of its own.**
Recomputing fresh each call means there's only ever one source of truth for the underlying
numbers, matching this project's "don't build a second thing that can drift from the first"
posture used everywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_dfs.storage.agent_results_store import AgentResultRow, read_agent_results
from nfl_dfs.storage.contest_results_store import read_contest_results
from nfl_dfs.tracking.contest_placement_estimate import estimate_agent_placements

REAL_AGENT_IDS = frozenset(
    {"chalk_anchor", "game_script_architect", "matchup_purist", "arbitrageur", "explosion_shootout", "volatility_engine"}
)


@dataclass(frozen=True)
class SeasonRecord:
    """One tracked entity's record across every week logged so far this season -- either an
    operator-played lineup (`agent_id` is its own label, e.g. "L1") or one of the 6 real agents."""

    agent_id: str  # "L1"/"L2"/... for an operator lineup, or a real NflAgentConstructor.agent_id
    is_operator_lineup: bool
    weeks_tracked: int  # distinct weeks with >=1 scored row for this entity
    avg_delta: float | None  # mean (actual - proj) across scored weeks; None if none scored yet
    best_week: tuple[int, float] | None  # (week, total_dk_score) of the single best scored week
    worst_week: tuple[int, float] | None
    wins: int  # weeks with >=1 real-or-estimated cash -- see module docstring
    contest_entries: int  # real (operator lineups) or estimated (agents) contest entries, summed
    contest_cashes: int  # real or estimated cashes among contest_entries
    basis: str  # "real" (an operator lineup's own DK entries) or "estimated" (an agent, interpolated)


def compute_season_records(season: int, *, agent_path=None, contest_path=None) -> list[SeasonRecord]:
    all_rows = read_agent_results(season=season, path=agent_path)

    def track_key(row: AgentResultRow) -> str:
        return row.strategy_name if row.agent_id == "operator" else row.agent_id

    by_track: dict[str, list[AgentResultRow]] = {}
    for row in all_rows:
        by_track.setdefault(track_key(row), []).append(row)

    contest_rows = read_contest_results(season=season, path=contest_path)
    contest_by_week_label: dict[tuple[int, str], list] = {}
    for c in contest_rows:
        contest_by_week_label.setdefault((c.week, c.lineup_label), []).append(c)

    # Estimated agent placements, computed once per (season, week) present in the data -- each
    # call needs that week's real agent scores and real contest anchors together.
    weeks = {r.week for r in all_rows}
    agent_scores_by_week: dict[int, dict[str, float]] = {}
    for week in weeks:
        agent_scores_by_week[week] = {
            r.agent_id: r.total_dk_score
            for r in all_rows
            if r.week == week and r.agent_id in REAL_AGENT_IDS and r.total_dk_score is not None
        }
    estimated_by_week: dict[int, list] = {
        week: estimate_agent_placements(season, week, scores, contest_path=contest_path) if scores else []
        for week, scores in agent_scores_by_week.items()
    }

    records = []
    for key, rows in by_track.items():
        is_operator = rows[0].agent_id == "operator"
        row_weeks = {r.week for r in rows}
        scored = [r for r in rows if r.total_dk_score is not None]
        deltas = [r.total_dk_score - r.proj_total for r in scored]
        best_week = max(((r.week, r.total_dk_score) for r in scored), key=lambda t: t[1], default=None)
        worst_week = min(((r.week, r.total_dk_score) for r in scored), key=lambda t: t[1], default=None)

        entries = 0
        cashes = 0
        cash_weeks = 0
        if is_operator:
            for week in row_weeks:
                week_entries = contest_by_week_label.get((week, key), [])
                week_cashes = sum(1 for c in week_entries if c.cashed)
                entries += len(week_entries)
                cashes += week_cashes
                cash_weeks += int(week_cashes > 0)
            basis = "real"
        else:
            for week in row_weeks:
                week_placements = [p for p in estimated_by_week.get(week, []) if p.agent_id == key]
                week_cashes = sum(1 for p in week_placements if p.estimated_cashed)
                entries += len(week_placements)
                cashes += week_cashes
                cash_weeks += int(week_cashes > 0)
            basis = "estimated"

        records.append(
            SeasonRecord(
                agent_id=key,
                is_operator_lineup=is_operator,
                weeks_tracked=len(row_weeks),
                avg_delta=round(sum(deltas) / len(deltas), 2) if deltas else None,
                best_week=best_week,
                worst_week=worst_week,
                wins=cash_weeks,
                contest_entries=entries,
                contest_cashes=cashes,
                basis=basis,
            )
        )

    records.sort(key=lambda r: (-r.wins, -(r.contest_cashes / r.contest_entries if r.contest_entries else -1), -(r.avg_delta or float("-inf"))))
    return records
