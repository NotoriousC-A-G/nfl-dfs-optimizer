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
    # GUIDANCE, not rules (Chris, 2026-10-09): the usual bar for the agent, stated in its brief; going below it is the expert's call with a stated
    # reason. The validator records a WARNING when a design falls below it.
    must_back_probability: float | None = None  # usually a backed branch has at least this probability
    min_game_total: float | None = None  # usually every game this agent stacks in has at least this total
    # The floor dial (draft, not backtested; Chris 2026-10-07: do not lean only on ceiling -- boom-or-bust weeks are mostly bust).
    # In [-1, 1] = 2 * floor_share - 1, so a 60/40 floor/ceiling tilt is +0.2 and 30/70 is -0.4. A tilt of the value the solver
    # maximizes (`value/tail_value.py`), never a constraint.
    floor_lean: float = 0.0


POOL_AGENTS: tuple[PoolAgentSpec, ...] = (
    PoolAgentSpec(
        "shootout_stack", "Shootout Stack",
        "Believes in games where BOTH offenses function (high-scoring). Its bets are typically a QB with at least two of his pass catchers plus a "
        "bring-back from the opposing offense, in the top games on the slate by total, and it can hold a second bet elsewhere (an RB with his own "
        "defense, another offense's receivers). Bring-backs are allowed. Leans to ceiling.",
        PoolRules(min_core=4), floor_lean=-0.4,
    ),
    PoolAgentSpec(
        "contrarian_game", "Contrarian Game",
        "Game-level leverage: bets in games with a LIVE environment (a total around 44 or better is the usual bar; go below it only with a stated "
        "reason) that the field is under-invested in (low ownership relative to its environment score and stack viability). Never pick a dead game "
        "just because it is low-owned. Bring-backs allowed.",
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
        "Believes a game where ONE offense breaks (pressure collapse, a QB injury, turnovers) so the OTHER side scores on short fields: its bets are that "
        "other side's defense, RB and pass catchers. A branch of roughly 25% or better is the usual bar; if you build it on a less likely branch, say why "
        "you still want it. If the slate has no such angle at all, mark the agent unavailable with a reason (expected some weeks).",
        PoolRules(min_core=3), must_back_probability=0.25,
    ),
    PoolAgentSpec(
        "volume_anchor", "Volume Anchor",
        "Locked-in individual volume: select on volume (carry share, target share, snaps, red-zone usage), NOT on projection, and you may use low-total "
        "games -- a grind is a fine home for a lead back with his defense or a target-hog TE with his QB. NO bring-backs (enforced in code). Do not "
        "pick the highest-projection players just because they are high-projection.",
        PoolRules(min_core=4, forbid_pass_catcher_bring_back=True, forbid_rb_bring_back=True), floor_lean=0.2,
    ),
)
POOL_AGENT_BY_ID = {a.agent_id: a for a in POOL_AGENTS}
