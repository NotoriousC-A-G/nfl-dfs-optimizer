"""NflAgentConstructor: deterministic parameter-vector presets over the existing ILP solver
(NflAgentConstructor Phase B, PRD's original lineup-construction-agents ask). Explicitly NOT live
LLM reasoning -- see `analysis/circumstance/` for the (separate, already-shipped) system that is.

`constructor.py` defines the `NflAgentConstructor` dataclass itself. `signal_bundle.py` joins every
real, already-computed per-player signal (`ceiling_multiplier`, `LeverageAssessment`,
`MatchupContextResult`, `StackContext`) into one read-only `SignalBundle`, reusing
`composition/player_detail.py`'s own private join helpers rather than re-deriving them.
`scoring.py`'s `compute_agent_objective_delta` turns one agent + a `SignalBundle` into the
`objective_delta_by_id` dict `optimizer/lineup.py`'s solver hook (PR B1) accepts -- a separate
delta dispatched on top of the base objective, never a direct write into `blended_projection`
itself, mirroring the sister MLB project's own resolved delta-dispatch design. `orchestrate.py`'s
`generate_agent_lineups` runs every `NFL_AGENTS` entry against one shared pool/`SignalBundle` and
provides the verification-first diagnostics (delta stats, an empty-delta wiring-bug check,
pairwise lineup overlap, Chalk Anchor's inertness proof) the approved plan calls for.
"""

from nfl_dfs.agents.constructor import EdgeCondition, NflAgentConstructor
from nfl_dfs.agents.orchestrate import (
    AgentDeltaStats,
    AgentLineupResult,
    agents_with_suspiciously_empty_deltas,
    chalk_anchor_matches_baseline,
    distinct_core_stack_count,
    generate_agent_lineups,
    pairwise_lineup_overlap,
    summarize_agent_deltas,
)
from nfl_dfs.agents.registry import (
    ARBITRAGEUR,
    CHALK_ANCHOR,
    EXPLOSION_SHOOTOUT,
    GAME_SCRIPT_ARCHITECT,
    MATCHUP_PURIST,
    NFL_AGENTS,
    VOLATILITY_ENGINE,
)
from nfl_dfs.agents.scoring import (
    CLOSE_SPREAD_THRESHOLD_POINTS,
    HIGH_TOTAL_THRESHOLD_POINTS,
    compute_agent_objective_delta,
)
from nfl_dfs.agents.signal_bundle import PlayerSignals, SignalBundle, build_signal_bundle

__all__ = [
    "ARBITRAGEUR",
    "CHALK_ANCHOR",
    "CLOSE_SPREAD_THRESHOLD_POINTS",
    "EXPLOSION_SHOOTOUT",
    "GAME_SCRIPT_ARCHITECT",
    "HIGH_TOTAL_THRESHOLD_POINTS",
    "MATCHUP_PURIST",
    "NFL_AGENTS",
    "VOLATILITY_ENGINE",
    "AgentDeltaStats",
    "AgentLineupResult",
    "EdgeCondition",
    "NflAgentConstructor",
    "PlayerSignals",
    "SignalBundle",
    "agents_with_suspiciously_empty_deltas",
    "build_signal_bundle",
    "chalk_anchor_matches_baseline",
    "compute_agent_objective_delta",
    "distinct_core_stack_count",
    "generate_agent_lineups",
    "pairwise_lineup_overlap",
    "summarize_agent_deltas",
]
