"""Build an agent's lineups one VARIATION at a time (Chris, 2026-10-09).

An agent has 1-3 variations. Each is a set of beliefs across the slate -- a core stack, at most one view per game, and (agent-wide) a spend
plan by position -- and builds ONE lineup:

- tiers come from the variation (`pool/derive.py`; the expert's explicit tiers adjust them);
- every player is priced under his own game's view (`conditional_multipliers`; a game with no view uses the full mix);
- the spend plan tilts value by salary: a position marked "pay" gets +`SPEND_TILT` value points per $1K of salary, "value" gets -`SPEND_TILT`
  (a draft tilt, not backtested -- the slate's position economics is what the expert reads to choose the plan).

With fewer variations than lineups they repeat in order, kept apart by min-player-difference and the reuse discounts. A failure in any
variation's build raises `PoolBuildFailure` for the whole agent (the expert loop then repairs it).
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_dfs.build.evidence.contracts import EvidencePacket
from nfl_dfs.build.pool.contracts import ExpertAgentOutput, PlayerRef, Variation
from nfl_dfs.build.pool.derive import derive_tiers, dst_multipliers
from nfl_dfs.build.pool.expand import expand_pool
from nfl_dfs.build.thesis.contracts import GameThesis, PairSign
from nfl_dfs.build.value.calibration import CalibrationTable
from nfl_dfs.build.value.tail_value import conditional_multipliers, tail_values
from nfl_dfs.optimizer.pool_solve import PoolBuildFailure, PoolBuildResult, build_agent_lineups
from nfl_dfs.projection.blend import PlayerProjection

SPEND_TILT = 0.4  # value points per $1K of salary, positive for "pay" and negative for "value"


@dataclass(frozen=True)
class VariationBuild:
    """`result.lineups[i]` was built for `variations[i]`; `pools[i]` is the pool used for it."""

    result: PoolBuildResult
    variations: tuple[Variation, ...]
    pools: tuple


def spend_tilt(values: dict[str, float], projections: list[PlayerProjection], spend_plan: tuple[tuple[str, str], ...]) -> dict[str, float]:
    plan = dict(spend_plan)
    sign = {"pay": 1.0, "value": -1.0}
    out = dict(values)
    for p in projections:
        s = sign.get(plan.get(p.position, "neutral"))
        if s and p.canonical_id in out and p.salary:
            out[p.canonical_id] += s * SPEND_TILT * p.salary / 1000.0
    return out


def build_agent_by_variation(
    output: ExpertAgentOutput,
    *,
    universe: list[PlayerRef],
    theses: dict[str, GameThesis],
    packets: dict[str, EvidencePacket],
    projections: list[PlayerProjection],
    table: CalibrationTable,
    floor_lean: float,
    n: int,
    opponent_of: dict[str, str],
    game_id_by_team: dict[str, str],
    pair_signs: list[PairSign],
    avoid_lineups: list[frozenset[str]],
    min_player_difference: int = 3,
) -> VariationBuild:
    if not output.variations:
        raise PoolBuildFailure(output.agent_id, "pool_validation", "the agent has no variation (stack + views) to build a lineup for", {})
    inputs = [(p.canonical_id, p.position, p.blended_projection) for p in projections]
    avoid = list(avoid_lineups)
    lineups, used, pools, warnings, last = [], [], [], [], None
    for i in range(n):
        var = output.variations[i % len(output.variations)]
        try:
            derived = derive_tiers(universe, theses, packets, var.views, var.stack)
        except ValueError as exc:
            raise PoolBuildFailure(output.agent_id, "pool_validation", str(exc), {}) from exc
        pool = expand_pool(output, universe, derived)
        mult = conditional_multipliers(theses.values(), var.views, output.build_thesis.avoids)
        mult = {**mult, **dst_multipliers(universe, mult)}  # a defense moves opposite to the QB it faces
        values = {k: v.tv for k, v in tail_values(inputs, table, multipliers=mult, floor_lean=floor_lean).items()}
        values = spend_tilt(values, projections, output.spend_plan)
        res = build_agent_lineups(
            pool, projections, values, n=1, opponent_of=opponent_of, game_id_by_team=game_id_by_team, pair_signs=pair_signs,
            avoid_lineups=avoid, min_player_difference=min_player_difference, required_ids=var.stack,
        )
        lu = res.lineups[0]
        lineups.append(lu)
        used.append(var)
        pools.append(res.pool)
        avoid.append(frozenset(p.canonical_id for p in lu.players))
        warnings.extend(res.warnings)
        last = res
    merged = PoolBuildResult(output.agent_id, tuple(lineups), pools[0], last.report, tuple(warnings))
    return VariationBuild(merged, tuple(used), tuple(pools))
