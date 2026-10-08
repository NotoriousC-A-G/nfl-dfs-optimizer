"""Build an agent's lineups ONE SCRIPT AT A TIME (Chris, 2026-10-09).

An agent has a core thesis and 1-3 script variations (`build_thesis.backs`, each one branch of one game). Each lineup is built for a single
script: the pool's tiers for that script come from `pool/derive.py` (the expert's explicit tiers adjust them), and every player is priced under
that script (`conditional_multipliers`), so a lineup is the best lineup in the world where its script happens -- never a blend of scripts that
cannot all happen. With fewer scripts than lineups the extra lineups reuse the scripts in order, kept apart by the earlier lineups'
min-player-difference and reuse discounts.

A failure in any script's build raises `PoolBuildFailure` for the whole agent (the expert loop then repairs the agent).
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_dfs.build.evidence.contracts import EvidencePacket
from nfl_dfs.build.pool.contracts import ExpertAgentOutput, PlayerRef
from nfl_dfs.build.pool.derive import derive_tiers
from nfl_dfs.build.pool.expand import expand_pool
from nfl_dfs.build.thesis.contracts import GameThesis, PairSign
from nfl_dfs.build.value.calibration import CalibrationTable
from nfl_dfs.build.value.tail_value import conditional_multipliers, tail_values
from nfl_dfs.optimizer.pool_solve import PoolBuildFailure, PoolBuildResult, build_agent_lineups
from nfl_dfs.projection.blend import PlayerProjection


@dataclass(frozen=True)
class ScriptedBuild:
    """`result.lineups[i]` was built for script `scripts[i]`; `pools[script]` is the pool used for it."""

    result: PoolBuildResult
    scripts: tuple[str, ...]
    pools: dict


def build_agent_by_script(
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
) -> ScriptedBuild:
    scripts = list(dict.fromkeys(output.build_thesis.backs))
    if not scripts:
        raise PoolBuildFailure(output.agent_id, "pool_validation", "the build thesis backs no branch, so there is no script to build for", {})
    inputs = [(p.canonical_id, p.position, p.blended_projection) for p in projections]
    avoid = list(avoid_lineups)
    lineups, used, pools, warnings, last = [], [], {}, [], None
    for i in range(n):
        ref = scripts[i % len(scripts)]
        try:
            derived = derive_tiers(universe, theses, packets, ref)
        except ValueError as exc:
            raise PoolBuildFailure(output.agent_id, "pool_validation", str(exc), {}) from exc
        pool = expand_pool(output, universe, derived)
        mult = conditional_multipliers(theses.values(), [ref], output.build_thesis.avoids)
        values = {k: v.tv for k, v in tail_values(inputs, table, multipliers=mult, floor_lean=floor_lean).items()}
        res = build_agent_lineups(
            pool, projections, values, n=1, opponent_of=opponent_of, game_id_by_team=game_id_by_team, pair_signs=pair_signs,
            avoid_lineups=avoid, min_player_difference=min_player_difference,
        )
        lu = res.lineups[0]
        lineups.append(lu)
        used.append(ref)
        pools[ref] = res.pool
        avoid.append(frozenset(p.canonical_id for p in lu.players))
        warnings.extend(res.warnings)
        last = res
    merged = PoolBuildResult(output.agent_id, tuple(lineups), pools[used[0]], last.report, tuple(warnings))
    return ScriptedBuild(merged, tuple(used), pools)
