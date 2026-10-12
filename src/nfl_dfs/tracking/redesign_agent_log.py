"""Forward-log the redesign pool agents' lineups into `agent_results.csv` (same store the legacy
agents and the operator use), so season records for the new agents start from the week they first
ran. Built from the saved build artifact (`storage/build_artifact_store.py`), so logging after the
fact is equivalent to logging at generation time. One row per agent (its first lineup); idempotent
on `(season, week, agent_id, strategy_name)` via `save_agent_results`. `total_dk_score` is left
blank for `score_and_backfill_agent_results` to fill, same "forward log now, join later" split.
"""

from __future__ import annotations

from nfl_dfs.build.agents import POOL_AGENTS
from nfl_dfs.storage.agent_results_store import AgentResultRow

_DISPLAY = {a.agent_id: a.display_name for a in POOL_AGENTS}


def build_redesign_agent_rows(artifact: dict, player_pool: list[dict], season: int, week: int) -> list[AgentResultRow]:
    pool_by_id = {(r.get("identity") or {}).get("canonical_id"): r for r in player_pool}
    rows = []
    for agent_id, agent in (artifact.get("agents") or {}).items():
        lineups = agent.get("lineups") or []
        if agent_id not in _DISPLAY or not lineups:
            continue
        lineup = lineups[0]
        tokens = []
        for cid in lineup.get("player_ids", []):
            row = pool_by_id.get(cid)
            if row is None:
                break
            tokens.append(f"{row['identity']['display_name']} ({row['position']}-{row['team']})")
        else:
            rows.append(
                AgentResultRow(
                    season=season,
                    week=week,
                    agent_id=agent_id,
                    strategy_name=_DISPLAY[agent_id],
                    proj_total=round(lineup["projected"], 2),
                    salary=int(lineup["salary"]),
                    players=tuple(tokens),
                )
            )
    return rows


def build_chalk_anchor_row(snapshot: dict, season: int, week: int) -> AgentResultRow | None:
    """Chalk Anchor -- still live after the redesign, but not part of the pool build artifact: it's
    the legacy projection-max baseline, emitted in the slate snapshot's `agent_lineups`. Logged the
    same way (idempotent via `save_agent_results`). None when the snapshot has no Chalk Anchor lineup."""
    for entry in snapshot.get("agent_lineups") or []:
        if (entry.get("agent") or {}).get("agent_id") != "chalk_anchor":
            continue
        lineup = entry["lineup"]
        return AgentResultRow(
            season=season,
            week=week,
            agent_id="chalk_anchor",
            strategy_name="Chalk Anchor",
            proj_total=round(lineup["total_projected_points"], 2),
            salary=int(lineup["total_salary"]),
            players=tuple(f"{p['display_name']} ({p['position']}-{p['team']})" for p in lineup["players"]),
        )
    return None
