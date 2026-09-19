"""`NFL_AGENTS`: the 6 lineup-construction agents Chris approved for NflAgentConstructor Phase B4.

Every numeric value here is a disclosed, draft starting point (`scoring.py`'s own
`_MAX_IQR_FRACTION_PER_AXIS`/threshold disclosure applies the same way to these agent-level
choices) -- not derived from a backtest. Phase C's retroactive backtesting against the real
2020-2025 ResultsDB is the intended path to validating or retuning these, not attempted here.
"""

from __future__ import annotations

from nfl_dfs.agents.constructor import NflAgentConstructor

CHALK_ANCHOR = NflAgentConstructor(
    agent_id="chalk_anchor",
    display_name="Chalk Anchor",
    # Every axis at its neutral 0.0 default -- the control/baseline. Doubles as this system's own
    # verification anchor: `compute_agent_objective_delta(CHALK_ANCHOR, ...)` must always return
    # `{}` (see `test_agents_scoring.py::test_chalk_anchor_produces_no_delta_at_all`), so its
    # generated lineup is provably identical to plain best-`blended_projection` generation.
)

GAME_SCRIPT_ARCHITECT = NflAgentConstructor(
    agent_id="game_script_architect",
    display_name="Game Script Architect",
    game_script_lean_weight=0.8,
    bring_back_allowed=True,
    edge_condition="close_spread_or_high_total",
)

MATCHUP_PURIST = NflAgentConstructor(
    agent_id="matchup_purist",
    display_name="Matchup Purist",
    matchup_conviction=0.8,
)

ARBITRAGEUR = NflAgentConstructor(
    agent_id="arbitrageur",
    display_name="Arbitrageur",
    ownership_stance=-0.9,  # leans hard contrarian/leverage.
)

EXPLOSION_SHOOTOUT = NflAgentConstructor(
    agent_id="explosion_shootout",
    display_name="Explosion/Shootout",
    bring_back_allowed=True,
    bring_back_rb_allowed=True,
    ceiling_lean=0.5,
    edge_condition="high_total",
)

VOLATILITY_ENGINE = NflAgentConstructor(
    agent_id="volatility_engine",
    display_name="Volatility Engine",
    # Chris's explicit framing: the "polar opposite of Chalk Anchor" -- maximum ceiling lean, a
    # real (but deliberately less extreme than Arbitrageur's dedicated leverage play) contrarian
    # ownership lean, and no bring-backs (a pure single-team ceiling play, not a game-stack one).
    ceiling_lean=1.0,
    ownership_stance=-0.5,
    bring_back_allowed=False,
)

NFL_AGENTS: list[NflAgentConstructor] = [
    CHALK_ANCHOR,
    GAME_SCRIPT_ARCHITECT,
    MATCHUP_PURIST,
    ARBITRAGEUR,
    EXPLOSION_SHOOTOUT,
    VOLATILITY_ENGINE,
]
