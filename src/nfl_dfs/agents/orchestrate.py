"""`generate_agent_lineups`: solve one lineup per `NflAgentConstructor` from the SAME pool
(NflAgentConstructor Phase B5) -- each agent gets its own independent ILP solve via
`optimizer/lineup.py`'s `objective_delta_by_id` hook (PR B1), no no-good-cut interaction between
agents (a stack Chalk Anchor picks is fair game for Volatility Engine too; only each agent's OWN
prior lineups, if a caller ever asked for more than one per agent, would be cut).

Also provides the "verification-first" diagnostics the approved plan calls for -- built into this
module (not deferred to Phase C's tracking data) after the sister MLB project's own real history:
a preference slider silently did nothing for months, undetected, until enough forward-logged
slates accumulated to notice by eye. These functions make that class of silent failure visible on
every single run instead of waiting on an accumulation of evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Sequence

from nfl_dfs.agents.constructor import NflAgentConstructor
from nfl_dfs.agents.registry import CHALK_ANCHOR, NFL_AGENTS
from nfl_dfs.agents.scoring import compute_agent_objective_delta
from nfl_dfs.agents.signal_bundle import SignalBundle
from nfl_dfs.optimizer.lineup import Lineup, generate_lineups
from nfl_dfs.projection.blend import PlayerProjection


@dataclass(frozen=True)
class AgentLineupResult:
    """One agent's real generated lineup plus the exact delta dict that produced it -- the delta
    is kept alongside the lineup (not discarded after the solve) specifically so the verification
    functions below can inspect it without re-deriving it.
    """

    agent: NflAgentConstructor
    lineup: Lineup
    delta_by_canonical_id: dict[str, float]


def generate_agent_lineups(
    pool: list[PlayerProjection],
    signal_bundle: SignalBundle,
    agents: Sequence[NflAgentConstructor] | None = None,
) -> list[AgentLineupResult]:
    """One independent `generate_lineups(pool, n=1, objective_delta_by_id=...)` solve per agent
    (`NFL_AGENTS` if `agents` isn't supplied). Raises `LineupGenerationError` (propagated straight
    from `generate_lineups`, not swallowed) if `pool` is infeasible at all -- every agent shares
    the same roster/salary/stack constraints, so an infeasible pool fails identically for all of
    them; there is no per-agent feasibility difference to catch here.
    """
    resolved_agents = list(agents) if agents is not None else NFL_AGENTS
    results: list[AgentLineupResult] = []
    for agent in resolved_agents:
        delta = compute_agent_objective_delta(agent, pool, signal_bundle)
        lineup = generate_lineups(pool, n=1, objective_delta_by_id=delta)[0]
        results.append(AgentLineupResult(agent=agent, lineup=lineup, delta_by_canonical_id=delta))
    return results


@dataclass(frozen=True)
class AgentDeltaStats:
    """Real summary stats over one agent's delta dict -- the per-run print the approved plan asks
    for ("each agent's real delta sum/min/max/mean across the pool"). All-zero fields for an
    agent with an empty delta dict, not `None` -- `n_players_affected == 0` is the real signal a
    caller should check, not a missing-data gap.
    """

    agent_id: str
    n_players_affected: int
    delta_sum: float
    delta_min: float
    delta_max: float
    delta_mean: float


def summarize_agent_deltas(results: list[AgentLineupResult]) -> list[AgentDeltaStats]:
    stats: list[AgentDeltaStats] = []
    for r in results:
        values = list(r.delta_by_canonical_id.values())
        if not values:
            stats.append(AgentDeltaStats(r.agent.agent_id, 0, 0.0, 0.0, 0.0, 0.0))
            continue
        stats.append(
            AgentDeltaStats(
                agent_id=r.agent.agent_id,
                n_players_affected=len(values),
                delta_sum=sum(values),
                delta_min=min(values),
                delta_max=max(values),
                delta_mean=sum(values) / len(values),
            )
        )
    return stats


def agents_with_suspiciously_empty_deltas(
    results: list[AgentLineupResult], *, chalk_anchor_agent_id: str = CHALK_ANCHOR.agent_id
) -> list[str]:
    """`agent_id`s (excluding the deliberately-always-inert Chalk Anchor) whose delta dict came
    back completely empty this run -- a likely wiring bug, not a legitimate "nothing to say this
    week": a real agent with at least one nonzero slider should almost always find SOME applicable
    signal across a real, ~200+ player slate pool. The approved plan's "hard, loud warning" --
    callers (the live script) print/raise on a nonempty result from this function; this function
    itself only detects the condition, it doesn't decide how loudly to surface it.
    """
    return [
        r.agent.agent_id
        for r in results
        if r.agent.agent_id != chalk_anchor_agent_id and not r.delta_by_canonical_id
    ]


def pairwise_lineup_overlap(results: list[AgentLineupResult]) -> dict[tuple[str, str], int]:
    """Real shared-player count between every distinct pair of agents' generated lineups, keyed by
    `(agent_id, agent_id)` -- the approved plan's "pairwise player-overlap count across the 6 real
    lineups."
    """
    overlap: dict[tuple[str, str], int] = {}
    for a, b in combinations(results, 2):
        ids_a = {p.canonical_id for p in a.lineup.players}
        ids_b = {p.canonical_id for p in b.lineup.players}
        overlap[(a.agent.agent_id, b.agent.agent_id)] = len(ids_a & ids_b)
    return overlap


def chalk_anchor_matches_baseline(
    results: list[AgentLineupResult],
    baseline_lineup: Lineup,
    *,
    chalk_anchor_agent_id: str = CHALK_ANCHOR.agent_id,
) -> bool:
    """The approved plan's core inertness proof: Chalk Anchor's generated lineup must be the exact
    same 9 players as `baseline_lineup` (plain best-`blended_projection` generation, no agent
    delta at all) -- `False` if Chalk Anchor isn't even in `results`, never a silent pass.
    """
    chalk = next((r for r in results if r.agent.agent_id == chalk_anchor_agent_id), None)
    if chalk is None:
        return False
    return {p.canonical_id for p in chalk.lineup.players} == {p.canonical_id for p in baseline_lineup.players}
