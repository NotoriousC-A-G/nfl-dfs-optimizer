"""`NflAgentConstructor`: one lineup-construction agent's parameter vector (NflAgentConstructor
Phase B3) -- a deterministic PRESET over the existing ILP solver, dispatched via `scoring.py`'s
`compute_agent_objective_delta` and `optimizer/lineup.py`'s `objective_delta_by_id` hook (PR B1).
Explicitly NOT live LLM reasoning -- see `analysis/circumstance/` for that, a separate, already-
shipped system this module does not touch.

Modeled on the sister MLB project's own `AgentConstructor` (sliders -1..+1, a separate delta
dispatched on top of the base objective rather than overwriting it, magnitudes scaled to the
pool's own real spread) -- but every field here is grounded in THIS project's own real,
already-computed signals (`signal_bundle.py`), not a blind port. See the
`nfl-agent-constructor-is-an-evolution-not-a-port` memory for why NFL's small-sample, all-22
nature calls for different field semantics than MLB's large-sample pitcher-vs-batter framing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Every slider is -1..+1; 0.0 is neutral/inert on that axis (a Chalk-Anchor-equivalent value).
_SLIDER_FIELDS: tuple[str, ...] = (
    "ceiling_lean",
    "ownership_stance",
    "matchup_conviction",
    "game_script_lean_weight",
    "stack_conviction",
)

EdgeCondition = Literal["close_spread", "high_total", "close_spread_or_high_total"]


@dataclass(frozen=True)
class NflAgentConstructor:
    """One agent's parameter vector, dispatched via `compute_agent_objective_delta`.

    `ceiling_lean` -> Component A `ceiling_multiplier` (RB/WR only, ADR-0028) -- positive leans
        into upside variance, negative fades it.
    `ownership_stance` -> `LeverageAssessment.ownership_percentile` -- negative leans contrarian/
        leverage (boosts low-owned, fades high-owned), positive leans chalk (the reverse).
    `matchup_conviction` -> `MatchupContext.combined_multiplier` -- positive leans into favorable
        matchups and fades unfavorable ones, negative the reverse (a "matchup-skeptic" lean).
    `game_script_lean_weight` -> a real stack candidate's own game's `GameScriptLean.intensity`
        (close games boost more; blowouts dampen) -- only applies to players `signal_bundle.py`
        marks as this game's real primary-stack/bring-back candidates.
    `stack_conviction` -> the same real stack-candidate players' `StackProfile.
        game_stack_viability` -- a separate lean from `game_script_lean_weight` (overall game-
        environment quality for stacking, not specifically close-game intensity).
    `bring_back_allowed`/`bring_back_rb_allowed` -- hard on/off gates for whether a bring-back
        candidate's (WR/TE or RB respectively) boost is included at all for this agent; never a
        magnitude, since "does this agent build bring-backs at all" is a yes/no construction
        choice, not something a slider should approximate.
    `edge_condition` -- optional, gates `game_script_lean_weight`/`stack_conviction`'s
        contribution to only the games matching this real, named condition (see `scoring.py`'s
        `_game_matches_edge_condition`); `None` applies with no game-level gate.

    **Every numeric value here is a disclosed, draft starting point (ADR-0019/0020's "draft, not
    backtested" convention) -- not derived from a backtest.** Phase C's retroactive backtesting
    against the real 2020-2025 ResultsDB is the intended path to validating or retuning these.
    """

    agent_id: str
    display_name: str
    ceiling_lean: float = 0.0
    ownership_stance: float = 0.0
    matchup_conviction: float = 0.0
    game_script_lean_weight: float = 0.0
    stack_conviction: float = 0.0
    bring_back_allowed: bool = False
    bring_back_rb_allowed: bool = False
    edge_condition: EdgeCondition | None = None

    def __post_init__(self) -> None:
        for field_name in _SLIDER_FIELDS:
            value = getattr(self, field_name)
            if not -1.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be within -1.0..1.0, got {value!r}")
