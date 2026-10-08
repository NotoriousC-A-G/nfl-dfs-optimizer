"""Data contracts for an agent's **pool** (see `CONTEXT.md`): the players one builder agent may draw
from for a slate, arranged in tiers by the expert. The expert emits compact *group-level* tiers plus
named per-player overrides (`ExpertAgentOutput`); `expand.py` turns that into one `PoolEntry` per
player, and `validate.py` checks the result before the solver ever sees it.
"""

from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = 1
# Graded confidence, not a fence (Chris, 2026-10-08): core = the players the agent's stand is built on; eligible = fits the stand;
# reach = usable at a value haircut (a salary/position fill, or a deliberate shot such as a quiet game with a real chance of a
# shootout); exclude = barred, only for a stated reason. Unnamed players default to reach.
TIERS = ("core", "eligible", "reach", "exclude")


@dataclass(frozen=True)
class PlayerRef:
    """What the pool layer needs to know about one slate player."""

    canonical_id: str
    name: str
    team: str
    position: str  # QB | RB | WR | TE | DST
    salary: int | None
    projection: float | None
    status: str | None  # injury status in the DK vocabulary after the Q/override pass (e.g. "Q_CLEARED", "OUT")
    game_id: str | None = None


@dataclass(frozen=True)
class GroupTier:
    """Assign a tier to every player matching ALL set selector fields (None = don't care)."""

    tier: str
    reason: str
    team: str | None = None
    position: str | None = None
    game_id: str | None = None


@dataclass(frozen=True)
class PoolEntry:
    canonical_id: str
    tier: str  # "core" | "eligible" | "exclude"
    reason: str  # required for core and exclude


@dataclass(frozen=True)
class BuildThesis:
    """The agent's stated bet: which angles it backs/avoids/hedges (as "game_id:branch_id" refs), the
    stack it is built around (canonical ids), and why."""

    backs: tuple[str, ...]
    avoids: tuple[str, ...]
    hedges: tuple[str, ...]
    stack_anchor: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class PoolRules:
    min_core: int = 4  # a lineup must hold at least this many core-tier players
    forbid_pass_catcher_bring_back: bool = False  # hard slider -> solver constraint (never the expert's call)
    forbid_rb_bring_back: bool = False


@dataclass(frozen=True)
class ExpertAgentOutput:
    agent_id: str
    build_thesis: BuildThesis
    default_tier: str
    group_tiers: tuple[GroupTier, ...] = ()
    overrides: tuple[PoolEntry, ...] = ()
    rules: PoolRules = PoolRules()


@dataclass(frozen=True)
class Pool:
    schema_version: int
    agent_id: str
    build_thesis: BuildThesis
    entries: tuple[PoolEntry, ...]
    rules: PoolRules = PoolRules()
    widened_steps: tuple[str, ...] = ()  # every widening applied, with the check that triggered it
