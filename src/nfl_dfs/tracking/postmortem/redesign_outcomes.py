"""Grade the redesigned pipeline's own lineups (ADR-0046, decision 11) against real settled points.

The legacy post-mortem (`replay.py`) reads the old `agent_lineups` in the slate snapshot. The
redesigned build saves what it produced -- each agent's lineups, status and thesis -- in a separate
build artifact (`storage/build_artifact_store.py`). This module scores those lineups from the same
canonical_id-keyed actuals map every other post-mortem module reads, so the two are comparable.

A player with no settled score is left unresolved, never defaulted to 0.0 (same "absence, not zero"
rule as the rest of the post-mortem); a lineup with any unresolved player gets `actual_total=None`.
An agent that could not be built is reported as such -- "no lineup and why" is itself a result.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RedesignPlayer:
    canonical_id: str
    display_name: str
    team: str
    position: str
    actual: float | None


@dataclass(frozen=True)
class RedesignLineupOutcome:
    agent_id: str
    status: str
    thesis: str  # the agent's build_thesis reason, "" when it didn't build
    views: tuple[str, ...]
    projected_total: float | None
    actual_total: float | None
    delta: float | None
    salary: int | None
    players: tuple[RedesignPlayer, ...]
    unresolved: tuple[str, ...]
    note: str  # failure diagnosis or warnings, "" when clean


def score_redesign_lineups(
    artifact: dict, player_pool: list[dict], actual_by_id: dict[str, float]
) -> list[RedesignLineupOutcome]:
    info = {}
    for row in player_pool:
        identity = row.get("identity") or {}
        if identity.get("canonical_id"):
            info[identity["canonical_id"]] = (identity.get("display_name"), row.get("team", ""), row.get("position", ""))

    outcomes: list[RedesignLineupOutcome] = []
    for agent_id, agent in (artifact.get("agents") or {}).items():
        status = agent.get("status", "unknown")
        thesis = ((agent.get("build_thesis") or {}).get("reason")) or ""
        lineups = agent.get("lineups") or []
        if not lineups:
            failure = agent.get("first_failure")
            outcomes.append(
                RedesignLineupOutcome(agent_id, status, thesis, (), None, None, None, None, (), (), str(failure or "no lineup built"))
            )
            continue
        for lineup in lineups:
            players = []
            for pid in lineup.get("player_ids", []):
                name, team, position = info.get(pid, (pid, "", ""))
                players.append(RedesignPlayer(pid, name or pid, team, position, actual_by_id.get(pid)))
            unresolved = tuple(p.display_name for p in players if p.actual is None)
            actual_total = None if unresolved else round(sum(p.actual for p in players), 2)
            projected = lineup.get("projected")
            outcomes.append(
                RedesignLineupOutcome(
                    agent_id=agent_id,
                    status=status,
                    thesis=thesis,
                    views=tuple(lineup.get("views") or ()),
                    projected_total=round(projected, 2) if projected is not None else None,
                    actual_total=actual_total,
                    delta=round(actual_total - projected, 2) if actual_total is not None and projected is not None else None,
                    salary=lineup.get("salary"),
                    players=tuple(players),
                    unresolved=unresolved,
                    note="; ".join(agent.get("warnings") or ()),
                )
            )
    return outcomes
