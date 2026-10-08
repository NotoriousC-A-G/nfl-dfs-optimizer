"""The pool-built builder agents (Chris, 2026-10-07: six agents). **Chalk Anchor** (agent 1) stays on the
old projection-max path as the baseline and is NOT pooled. The five below each get their own pool from the
expert, defined by the angle they back -- not by an attitude -- with the football review's differentiators
as explicit constraints on their pools (`CONTEXT.md`: agent, pool, tier, build thesis, angle).

Hard sliders live HERE, in code (`PoolRules.forbid_*`), never in the expert's hands.
"""

from __future__ import annotations

from dataclasses import dataclass

from nfl_dfs.build.pool.contracts import PoolRules

CHALK_ANCHOR_ID = "chalk_anchor"


@dataclass(frozen=True)
class PoolAgentSpec:
    agent_id: str
    display_name: str
    brief: str  # what the expert is told to build
    rules: PoolRules  # hard sliders + default core minimum
    must_back_probability: float | None = None  # a backed branch must have at least this probability, else the agent is unavailable
    min_game_total: float | None = None  # every game this agent backs must have at least this total
    # The floor dial (draft, not backtested; Chris 2026-10-07: do not lean only on ceiling -- boom-or-bust weeks are mostly bust).
    # In [-1, 1] = 2 * floor_share - 1, so a 60/40 floor/ceiling tilt is +0.2 and 30/70 is -0.4. A tilt of the value the solver
    # maximizes (`value/tail_value.py`), never a constraint.
    floor_lean: float = 0.0


POOL_AGENTS: tuple[PoolAgentSpec, ...] = (
    PoolAgentSpec(
        "shootout_stack", "Shootout Stack",
        "Backs the branch in which BOTH offenses function (a high-scoring game). Choose from the top ~2 games on the slate by total. "
        "Build around ONE game: a QB, at least two of his pass catchers and a bring-back from the opposing offense. Bring-backs are allowed. "
        "Core = that game's QB, receivers and bring-back candidates; fill the rest of the lineup from other games.",
        PoolRules(min_core=4), floor_lean=-0.4,
    ),
    PoolAgentSpec(
        "contrarian_game", "Contrarian Game",
        "Game-level leverage: pick a game with a LIVE environment (total >= 44) that the field is under-invested in (low ownership relative to its "
        "environment score and stack viability) and stack it. Never pick a dead game just because it is low-owned. Bring-backs allowed.",
        PoolRules(min_core=4), floor_lean=-0.4, min_game_total=44.0,
    ),
    PoolAgentSpec(
        "chalk_pivot", "Chalk Pivot",
        "Hold the CHALK game (the one most of the field will stack) but pivot off the crowd: EXCLUDE that game's three highest-owned QB/WR/RB and use "
        "similar-role, lower-owned players from the same game instead. Your lineup may share at most 4 players with Chalk Anchor. Bring-backs allowed.",
        PoolRules(min_core=4),
    ),
    PoolAgentSpec(
        "short_field", "Short Field",
        "Backs a branch where ONE offense breaks (pressure collapse, a QB injury, turnovers) so the OTHER side scores on short fields: core = that other side's "
        "defense, RB and pass catchers. Only build this if some game has such a branch with probability >= 25%; otherwise set \"unavailable\": true with a "
        "reason -- the run will report that this agent cannot be built this slate (that is expected some weeks).",
        PoolRules(min_core=3), must_back_probability=0.25,
    ),
    PoolAgentSpec(
        "volume_anchor", "Volume Anchor",
        "Locked-in individual volume: select on volume (carry share, target share, snaps, red-zone usage), NOT on projection, and you may use low-total "
        "games -- a grind is a fine home for a lead back, a target-hog TE or a defense. Only the required QB + pass-catcher stack; NO bring-backs "
        "(this is enforced in code). Do not pick the highest-projection players just because they are high-projection.",
        PoolRules(min_core=4, forbid_pass_catcher_bring_back=True, forbid_rb_bring_back=True), floor_lean=0.2,
    ),
)
POOL_AGENT_BY_ID = {a.agent_id: a for a in POOL_AGENTS}
