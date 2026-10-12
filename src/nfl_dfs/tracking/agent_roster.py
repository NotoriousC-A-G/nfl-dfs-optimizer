"""Which agents are live vs retired -- the NFL analog of MLB's `AGENTS`-registry check in its
post-mortem (a row is "archived" when its agent_id is no longer in the registry, but the CSV keeps
every historical agent_id so the record is retained; archived rows collapse into a drawer).

After the ADR-0046 redesign the live roster is Chalk Anchor (the projection-max baseline, not
pooled) plus the five pool agents in `build/agents.py`. The other five legacy agents
(game_script_architect, matchup_purist, arbitrageur, explosion_shootout, volatility_engine) were
replaced; their `agent_results.csv` history stays, and the new agents track from week 5 forward.
The operator's own L1/L2/L3 are always live (they're logged under agent_id "operator").
"""

from __future__ import annotations

from nfl_dfs.agents.registry import NFL_AGENTS
from nfl_dfs.build.agents import CHALK_ANCHOR_ID, POOL_AGENTS

OPERATOR_ID = "operator"
ACTIVE_AGENT_IDS = frozenset({CHALK_ANCHOR_ID, *(a.agent_id for a in POOL_AGENTS)})
ALL_AGENT_IDS = ACTIVE_AGENT_IDS | frozenset(a.agent_id for a in NFL_AGENTS)

_DISPLAY_NAMES = {a.agent_id: a.display_name for a in NFL_AGENTS} | {a.agent_id: a.display_name for a in POOL_AGENTS}


def is_active_agent(agent_id: str) -> bool:
    """True for the operator and every agent in the live roster; False for a retired one."""
    return agent_id == OPERATOR_ID or agent_id in ACTIVE_AGENT_IDS


def agent_display_name(agent_id: str) -> str:
    return _DISPLAY_NAMES.get(agent_id, agent_id)
