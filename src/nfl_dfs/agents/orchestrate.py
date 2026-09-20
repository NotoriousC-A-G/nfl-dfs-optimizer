"""`generate_agent_lineups`: solve one lineup per `NflAgentConstructor` from the SAME pool
(NflAgentConstructor Phase B5) -- each agent gets its own ILP solve via `optimizer/lineup.py`'s
`objective_delta_by_id` hook (PR B1), WITH a cross-agent no-good cut: every earlier agent's core
stack (this run, in `agents` order) is forbidden from every later agent's solve, via
`generate_lineups`' `seed_core_stacks` param. This is the exact same no-good-cut technique
`generate_lineups`' own `n > 1` loop already uses to keep ONE agent's multiple lineups distinct,
just extended across agents.

**Why this exists (real finding, not a hypothetical):** the first live run of this feature (six
agents, real week 2 2026 slate) produced only 2 distinct core stacks across 6 agents -- Chalk
Anchor/Matchup Purist/Explosion-Shootout all landed on the same QB+WR combo, and Game Script
Architect/Arbitrageur both landed on a different single shared combo. Chris's own framing:
"you're modeling outcomes and the range of outcomes, [it's] not that narrow" -- the whole point of
six agents is to sample a real range of correlated-outcome theses; two (or four) of them silently
collapsing onto the identical stack defeats that. A skinny stack repeating alongside a different
main stack is a normal, deliberate DFS pattern; six agents landing on two stacks total is not.

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
from nfl_dfs.analysis.dup_risk_calibration import DupRiskLookupTable, classify_avg_ownership
from nfl_dfs.analysis.gpp_grade import GppGrade, compute_gpp_grade, compute_max_ceiling_weighted_total
from nfl_dfs.optimizer.lineup import (
    MIN_OWNERSHIP_COVERAGE,
    Lineup,
    LineupGenerationError,
    _avg_projected_ownership,
    generate_lineups,
)
from nfl_dfs.projection.blend import PlayerProjection


@dataclass(frozen=True)
class AgentLineupResult:
    """One agent's real generated lineup plus the exact delta dict that produced it -- the delta
    is kept alongside the lineup (not discarded after the solve) specifically so the verification
    functions below can inspect it without re-deriving it.

    `achieved_bucket`/`gpp_grade` (added 2026-09-20, the same day the separate ownership-bucket-
    *targeting* mechanism in `optimizer/lineup.py` was retired as the dashboard's primary lineup
    source -- Chris: "if we have 6 agents, in theory we should get 6 lineups") are real,
    DESCRIPTIVE reads of where this agent's own thesis-driven lineup actually landed -- never a
    target this function tries to hit. An agent's ownership tier here is a genuine byproduct of
    its own preference axes (Arbitrageur's `ownership_stance=-0.9` naturally produces a low-
    ownership build), not a blind-oversample search for one. Both are `None` when the caller
    didn't supply enough to compute them (`dup_risk_table`/`game_count` below) -- never guessed.
    """

    agent: NflAgentConstructor
    lineup: Lineup
    delta_by_canonical_id: dict[str, float]
    core_stack_forced_unique: bool
    achieved_bucket: int | None = None
    gpp_grade: GppGrade | None = None


def _ownership_and_ceiling_from_signal_bundle(signal_bundle: SignalBundle) -> tuple[dict[str, float], dict[str, float]]:
    """Real per-player projected ownership and `ceiling_multiplier`, read directly off the SAME
    `SignalBundle` every agent's own delta already used -- no second source of truth, no re-fetch.
    """
    ownership: dict[str, float] = {}
    ceiling: dict[str, float] = {}
    for canonical_id, signals in signal_bundle.signals_by_canonical_id.items():
        if signals.leverage is not None:
            ownership[canonical_id] = signals.leverage.projected_ownership
        if signals.ceiling_multiplier is not None:
            ceiling[canonical_id] = signals.ceiling_multiplier
    return ownership, ceiling


def generate_agent_lineups(
    pool: list[PlayerProjection],
    signal_bundle: SignalBundle,
    agents: Sequence[NflAgentConstructor] | None = None,
    *,
    dup_risk_table: DupRiskLookupTable | None = None,
    game_count: int | None = None,
) -> list[AgentLineupResult]:
    """One `generate_lineups(pool, n=1, objective_delta_by_id=...)` solve per agent (`NFL_AGENTS`
    if `agents` isn't supplied), each seeded with every EARLIER agent's `core_stack` (this run, in
    `agents` order) as an extra no-good cut -- so two different agents can no longer silently land
    on the identical QB+pass-catcher combination. The first agent in the list (`Chalk Anchor` by
    default) always solves unconstrained, since it's the control/baseline and must stay the true
    best-projection build, not a diversity-adjusted one.

    `dup_risk_table` and `game_count`, when both supplied, attach a real, descriptive
    `achieved_bucket`/`gpp_grade` to every result (see `AgentLineupResult`'s own docstring) --
    `compute_max_ceiling_weighted_total` (one extra real ILP solve) runs ONCE per pool here, never
    per-agent, since it's a property of the pool, not of any one agent's build.

    Raises `LineupGenerationError` (propagated straight from `generate_lineups`, not swallowed) if
    `pool` is infeasible at all -- every agent shares the same roster/salary/stack constraints, so
    an infeasible pool fails identically for all of them regardless of any diversity seed.

    A LATER agent can, in principle, run out of legal distinct-core-stack rosters once enough
    earlier agents have claimed one each (astronomically unlikely on a real ~13-game slate with
    ~26 team-side QB options, but not impossible on a thin slate) -- when that happens for one
    specific agent, this falls back to that agent's own unconstrained solve rather than dropping
    it from the run entirely (`core_stack_forced_unique=False` on that one result marks it).
    """
    resolved_agents = list(agents) if agents is not None else NFL_AGENTS
    ownership_by_id, ceiling_by_id = _ownership_and_ceiling_from_signal_bundle(signal_bundle)
    max_ceiling_weighted_total = (
        compute_max_ceiling_weighted_total(pool, ceiling_by_id) if game_count is not None else None
    )

    results: list[AgentLineupResult] = []
    used_core_stacks: list[frozenset[str]] = []
    for agent in resolved_agents:
        delta = compute_agent_objective_delta(agent, pool, signal_bundle)
        try:
            lineup = generate_lineups(
                pool, n=1, objective_delta_by_id=delta, seed_core_stacks=used_core_stacks
            )[0]
            forced_unique = True
        except LineupGenerationError:
            lineup = generate_lineups(pool, n=1, objective_delta_by_id=delta)[0]
            forced_unique = False

        achieved_bucket = None
        if dup_risk_table is not None:
            avg_own, covered = _avg_projected_ownership(lineup, ownership_by_id)
            if avg_own is not None and covered >= MIN_OWNERSHIP_COVERAGE:
                achieved_bucket, _, _ = classify_avg_ownership(avg_own, dup_risk_table)

        gpp_grade = None
        if game_count is not None:
            gpp_grade = compute_gpp_grade(
                lineup,
                projected_ownership_by_canonical_id=ownership_by_id,
                ceiling_multiplier_by_canonical_id=ceiling_by_id,
                max_ceiling_weighted_total=max_ceiling_weighted_total,
                game_count=game_count,
            )

        results.append(
            AgentLineupResult(
                agent=agent,
                lineup=lineup,
                delta_by_canonical_id=delta,
                core_stack_forced_unique=forced_unique,
                achieved_bucket=achieved_bucket,
                gpp_grade=gpp_grade,
            )
        )
        used_core_stacks.append(lineup.core_stack)
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


def distinct_core_stack_count(results: list[AgentLineupResult]) -> int:
    """How many of `results`' core stacks are actually distinct -- the direct, at-a-glance proof
    the cross-agent no-good cut is doing its job. With the cut in place this should equal
    `len(results)` on any real slate with enough team/QB options (every agent's
    `core_stack_forced_unique` is `True`); a value below that (only possible when at least one
    agent's cut had to be dropped via the infeasibility fallback -- see `generate_agent_lineups`'
    own docstring) is worth a second look, not silently accepted.
    """
    return len({r.lineup.core_stack for r in results})
